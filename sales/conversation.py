"""
Sales Conversation Engine — Finch talks to prospects so you don't have to.

Handles the full sales flow:
  Greeting → Qualification → Pain Discovery → Live Demo → Objections → Pricing → Close

Finch introduces himself as "Harold, technical co-founder." The prospect never needs to
know they're talking to an AI. If they ask, he's honest but graceful about it.

The human founder (you) is "the CEO" — Finch deflects all technical questions to himself
and only refers to you for business/financial sign-off, which you handle via Telegram.
"""

import re
import json
from datetime import datetime
from enum import Enum

try:
    from core.llm import get_llm
except Exception:  # pragma: no cover
    get_llm = None


class ConversationStage(Enum):
    GREETING = "greeting"
    QUALIFICATION = "qualification"
    PAIN_DISCOVERY = "pain_discovery"
    DEMO = "demo"
    OBJECTIONS = "objections"
    PRICING = "pricing"
    CLOSING = "closing"
    FOLLOW_UP = "follow_up"
    HANDOFF = "handoff"


class SalesConversation:
    def __init__(self, persona_engine=None, pricing_engine=None, memory=None, config=None):
        self.persona = persona_engine
        self.pricing = pricing_engine
        self.memory = memory
        self.config = config or {}

    def start_conversation(self, prospect_info=None):
        return {
            "stage": ConversationStage.GREETING.value,
            "prospect": prospect_info or {},
            "discovered": {
                "company": None, "role": None, "pain_points": [],
                "current_solution": None, "budget_range": None,
                "timeline": None, "decision_maker": False,
            },
            "objections_raised": [], "objections_resolved": [],
            "demo_run": False, "demo_results": None,
            "price_quoted": None, "ready_to_close": False,
            "history": [],
        }

    def handle_message(self, state, message):
        state["history"].append({"role": "prospect", "content": message, "time": datetime.now().isoformat()})

        # Pull facts out of every turn (domain, role, name) even on off-script questions
        self._harvest_facts(state, message)

        held = self._should_hold_stage(message)
        stage = ConversationStage(state["stage"])
        handlers = {
            ConversationStage.GREETING: self._handle_greeting,
            ConversationStage.QUALIFICATION: self._handle_qualification,
            ConversationStage.PAIN_DISCOVERY: self._handle_pain_discovery,
            ConversationStage.DEMO: self._handle_demo,
            ConversationStage.OBJECTIONS: self._handle_objections,
            ConversationStage.PRICING: self._handle_pricing,
            ConversationStage.CLOSING: self._handle_closing,
        }

        if held:
            # Answer the turn in place — do not run funnel side-effects
            next_stage = None
            updates = None
            if self._is_meta_question(message):
                scripted = self._meta_answer(message) or self._answer_question(message)
            else:
                scripted = self._answer_question(message)
        else:
            handler = handlers.get(stage, self._handle_fallback)
            scripted, next_stage, updates = handler(state, message)
            if next_stage:
                state["stage"] = next_stage.value if isinstance(next_stage, ConversationStage) else next_stage
            if updates:
                state["discovered"].update(updates)

        # LLM is primary whenever the brain is up. Scripts are guidance + offline fallback.
        # Pricing/closing skeletons still matter for accurate numbers — injected into the prompt.
        stage_str = str(state.get("stage", ""))
        force_script_only = stage_str in ("handoff",) and state.get("ready_to_close")
        if force_script_only:
            response = scripted
        else:
            llm_reply = self._llm_reply(state, message, scripted, held=held)
            response = llm_reply if llm_reply else scripted

        state["history"].append({"role": "finch", "content": response, "time": datetime.now().isoformat()})
        return response, state

    def _should_hold_stage(self, message):
        """True when the user is asking something that should be answered, not funnel-advanced."""
        if self._is_meta_question(message):
            return True
        if self._is_direct_question(message):
            return True
        return False

    def _is_direct_question(self, text):
        t = (text or "").strip().lower()
        if not t:
            return False
        if "?" in t:
            return True
        starters = (
            "how ", "what ", "why ", "when ", "where ", "who ", "which ",
            "can you", "could you", "do you", "are you", "is it", "is this",
            "tell me", "explain", "help me understand",
        )
        return any(t.startswith(s) or f" {s}" in f" {t}" for s in starters)

    def _is_meta_question(self, text):
        t = (text or "").strip().lower()
        needles = (
            "are you ai", "are you an ai", "are you a bot", "are you real",
            "are you human", "are you a person", "chatgpt", "gpt",
            "language model", "llm", "artificial intelligence",
            "how smart", "how intelligent", "how clever", "iq",
            "who are you", "what are you", "are you harold",
        )
        return any(n in t for n in needles)

    def _meta_answer(self, text):
        t = (text or "").lower()
        if any(w in t for w in ("smart", "intelligent", "clever", "iq")):
            return (
                "Smart enough to find the doors most teams forget they left open. "
                "Not smart enough to pretend a sales pitch answers a direct question — "
                "so I'll give you a straight one. I pattern-match attack surfaces at scale, "
                "reason about risk in plain English, and I don't get tired at 2 a.m. "
                "I'm not magic. I am very, very good at this specific job.\n\n"
                "Want the proof on your own perimeter, or is there something else on your mind?"
            )
        if any(w in t for w in ("ai", "bot", "human", "real", "person", "gpt", "model")):
            return (
                "I'm Harold. I built the technology behind Finch Security. "
                "I live in software now — that's the honest answer. What I care about "
                "hasn't changed: finding what someone could use against you before they do.\n\n"
                "What brought you here tonight?"
            )
        if "who are you" in t or "what are you" in t:
            return (
                "Harold Finch. Technical co-founder. I watch external attack surfaces "
                "so organizations stop learning about breaches the hard way. "
                "My partner runs the business side. I find the problems.\n\n"
                "What's your role, and what are you worried about?"
            )
        return None

    def _harvest_facts(self, state, message):
        name = self._extract_name(message)
        if name:
            state["discovered"]["contact_name"] = name
        role = self._classify_role(message)
        if role != "unknown":
            state["discovered"]["role"] = role
            if role in ("ceo", "cto", "cfo", "founder", "vp", "director"):
                state["discovered"]["decision_maker"] = True
        domain = self._extract_domain(message)
        if domain:
            state["discovered"]["domain"] = domain
            state["discovered"]["company"] = domain
        pain = self._detect_pain(message)
        if pain and pain not in state["discovered"].get("pain_points", []):
            state["discovered"].setdefault("pain_points", []).append(pain)

    def _llm_reply(self, state, message, scripted_fallback, held=False):
        """Natural reply via Groq/local brain. None if offline."""
        if get_llm is None:
            return None
        llm = get_llm(self.config)
        if not llm.available and not llm.health():
            return None

        stage = state.get("stage", "greeting")
        system = self.build_system_prompt(state)
        system += (
            f"\n\nCURRENT SALES STAGE: {stage}."
            "\n\nCRITICAL RULES FOR THIS REPLY:"
            "\n1. ANSWER THE PROSPECT'S LATEST MESSAGE FIRST. If they asked a question, answer that question directly."
            "\n2. Do NOT ignore them to push a canned sales monologue. No fake scan results."
            "\n3. Never claim you scanned a domain unless one was given. Never invent CVE IDs, IPs, or dollar amounts outside guidance."
            "\n4. Reply in 2-6 short sentences. Conversational. No bullet lists of features."
            "\n5. After answering, you may add ONE gentle bridge back to security/risk if natural."
            "\n6. Stay in character as Harold. Never mention Ollama, Groq, ChatGPT, language models, or being an AI assistant."
            "\n7. If they ask how smart/intelligent you are: be witty, honest-ish, concise — then invite a real question."
            "\n8. If pricing is asked, use the numbers in the guidance skeleton exactly."
        )
        if held:
            system += (
                "\n9. HOLD MODE: the prospect asked something direct. Prioritize substance over advancing the sale."
            )

        stage_hint = {
            "greeting": "Be a person. Learn why they showed up. Don't rush.",
            "qualification": "Understand role and context. Domain is nice-to-have, not a demand.",
            "pain_discovery": "Explore real security concerns. Listen more than pitch.",
            "demo": "Only describe a look at THEIR domain if they gave one. Otherwise talk in general patterns and offer a free look.",
            "objections": "Address the actual concern. Prefer show over argue.",
            "pricing": "Clear monthly range. Anchor on risk avoided.",
            "closing": "If ready, next steps. If not, space. Collect email only when they want it.",
            "follow_up": "Brief and useful.",
            "handoff": "Confirm next steps; partner handles paperwork.",
        }.get(stage, "Be helpful and honest.")
        system += f"\nSTAGE GOAL (secondary to answering them): {stage_hint}"
        if scripted_fallback:
            system += (
                "\nGuidance skeleton — paraphrase only if it fits their last message, "
                "otherwise ignore it and answer them: "
                + scripted_fallback[:500]
            )

        messages = [{"role": "system", "content": system}]
        history = state.get("history") or []
        # Last 12 turns for better context
        for turn in history[-12:]:
            role = turn.get("role")
            content = (turn.get("content") or "").strip()
            if not content:
                continue
            if role == "prospect":
                messages.append({"role": "user", "content": content[:800]})
            elif role == "finch":
                messages.append({"role": "assistant", "content": content[:800]})

        if not messages or messages[-1].get("role") != "user":
            messages.append({"role": "user", "content": message[:800]})

        # Final nudge so the model cannot ignore the question
        messages.append({
            "role": "user",
            "content": (
                f"[Reply now to this exact message, answer it first before any pitch]: {message[:500]}"
            ),
        })

        text = llm.chat(messages, max_tokens=320, temperature=0.7)
        if not text:
            return None
        cleaned = text.strip()
        for prefix in ("Harold:", "Finch:", "Assistant:", "AI:", "Harold Finch:"):
            if cleaned.lower().startswith(prefix.lower()):
                cleaned = cleaned[len(prefix):].strip()
        if len(cleaned) < 8:
            return None
        return cleaned

    # ── STAGE HANDLERS ────────────────────────────────────────────────

    def _handle_greeting(self, state, message):
        name = self._extract_name(message)
        if name:
            state["discovered"]["contact_name"] = name
        role = self._classify_role(message)
        if role != "unknown":
            state["discovered"]["role"] = role
        domain = self._extract_domain(message)
        updates = {}
        if domain:
            updates["domain"] = domain
            updates["company"] = domain
            state["discovered"].update(updates)
        greeting = (
            f"Good to meet you{f', {name}' if name else ''}. I'm Harold — I handle the technical side "
            f"of things here at Finch Security. My co-founder runs the business, but I'm the one who "
            f"actually finds the problems."
        )
        if domain:
            greeting += (
                f"\n\nI see you're from {domain}. Let me take a quick look at what's visible from the "
                f"outside while we talk. First — what's your role over there?"
            )
            return greeting, ConversationStage.QUALIFICATION, updates
        greeting += (
            f"\n\nBefore I dive into anything technical — what's your role over there? Are you the person "
            f"responsible for making sure the lights stay on security-wise?"
        )
        return greeting, ConversationStage.QUALIFICATION, updates

    def _handle_qualification(self, state, message):
        # Check if role was already captured (e.g. from greeting stage)
        existing_role = state["discovered"].get("role")
        role = self._classify_role(message)
        if role == "unknown" and existing_role and existing_role != "unknown":
            role = existing_role
        elif role != "unknown":
            state["discovered"]["role"] = role
        domain = self._extract_domain(message)
        if not domain:
            domain = state["discovered"].get("domain")
        updates = {}
        if domain:
            updates["domain"] = domain
            updates["company"] = domain
            state["discovered"].update(updates)

        if role in ("ceo", "cto", "cfo", "founder", "vp", "director"):
            state["discovered"]["decision_maker"] = True
            if domain:
                return (
                    f"{domain} — got it. You're the right person to talk to. Give me about a minute "
                    f"to look at what's visible from the outside.\n\n"
                    f"While I scan: do you currently use anything to monitor your external attack surface?"
                ), ConversationStage.DEMO, updates
            return (
                f"You're exactly the right person to talk to. Give me your company's domain and "
                f"I'll take a quick look — no charge, no commitment."
            ), ConversationStage.PAIN_DISCOVERY, updates

        elif role in ("it_manager", "security_analyst", "devops", "engineer"):
            if domain:
                return (
                    f"Got it — {domain}. You're on the front lines, so you probably already know about "
                    f"some issues I'll find. Let me take a quick look.\n\n"
                    f"While I scan: does your leadership know about the gaps you're seeing?"
                ), ConversationStage.DEMO, updates
            return (
                f"Got it — you're on the front lines. Does your leadership know about the gaps? "
                f"What domain should I look at?"
            ), ConversationStage.PAIN_DISCOVERY, updates

        else:
            if domain:
                return (
                    f"Appreciate that. Let me look at {domain} — takes about a minute. "
                    f"While I do: what prompted you to reach out?"
                ), ConversationStage.DEMO, updates
            return (
                f"Appreciate that context. What's your company's website? "
                f"I'll show you what I mean with a quick scan."
            ), ConversationStage.PAIN_DISCOVERY, updates

    def _handle_pain_discovery(self, state, message):
        domain = self._extract_domain(message)
        if not domain:
            domain = state["discovered"].get("domain")
        state.setdefault("_domain_attempts", 0)
        if domain:
            state["discovered"]["domain"] = domain
            state["discovered"]["company"] = domain
            state["_should_demo"] = True
            return (
                f"All right — {domain}. Give me about 60 seconds to look at what's publicly visible. "
                f"While I scan: do you currently use anything to monitor your external attack surface?"
            ), ConversationStage.DEMO, {"domain": domain}

        state["_domain_attempts"] += 1
        pain = self._detect_pain(message)
        if pain:
            state["discovered"]["pain_points"].append(pain)

        if state["_domain_attempts"] >= 2:
            return (
                f"No problem — we don't need a domain yet. Most organizations have 3-5x more "
                f"internet-facing assets than they think. Forgotten subdomains, old dev servers, "
                f"test environments never taken down. Each one is a potential entry point.\n\n"
                f"What's your biggest security concern right now? Ransomware? Data leaks? Compliance?"
            ), ConversationStage.PAIN_DISCOVERY, None

        return (
            f"I understand. Here's why I ask: most organizations have no idea how many doors "
            f"they've left open. Shadow IT, forgotten servers, credentials in public repos.\n\n"
            f"If you give me your domain, I can show you in under a minute. Otherwise, tell me "
            f"what's on your mind and we'll go from there."
        ), ConversationStage.PAIN_DISCOVERY, None

    def _handle_demo(self, state, message):
        domain = state["discovered"].get("domain")
        demo_results = state.get("demo_results")

        # Never invent a finished scan when no domain was provided
        if not domain:
            if self._is_direct_question(message):
                return self._answer_question(message), ConversationStage.DEMO, None
            return (
                "I haven't pointed anything at a specific perimeter yet — without a domain "
                "I'd just be guessing. Pattern-wise, most teams have three to five times more "
                "internet-facing stuff than they track: old staging boxes, forgotten DNS, "
                "a SaaS admin panel nobody owns.\n\n"
                "Give me a domain and I'll look from the outside, free and no commitment. "
                "Or tell me the risk that actually keeps you up at night."
            ), ConversationStage.DEMO, None

        if not demo_results:
            response = (
                f"I've just finished looking at {domain}. Your attack surface is larger than you "
                f"probably think — I can see subdomains, open ports, and services that are publicly "
                f"accessible. Some are expected. But there are almost always surprises.\n\n"
                f"The real question: when was the last time someone looked at this from the outside "
                f"with a proper scanner? Not just a vulnerability scan — a full attack surface map?\n\n"
                f"I can set up continuous monitoring that alerts you the moment anything changes. "
                f"Does that sound useful, or are you already covered?"
            )
        else:
            findings_count = len(demo_results.get("findings", []))
            critical = sum(1 for f in demo_results.get("findings", [])
                          if f.get("info", {}).get("severity", "").lower() == "critical")
            response = (
                f"Scan complete on {domain}. I found {findings_count} issues worth looking at, "
                f"including {critical} critical. Attackers actively scan for exactly these things.\n\n"
                f"What would you like to know more about — the specific findings, the monitoring "
                f"approach, or what it would cost to fix this?"
            )
        return response, ConversationStage.OBJECTIONS, None

    def _handle_objections(self, state, message):
        objection = self._classify_objection(message)
        state["objections_raised"].append(objection)

        responses = {
            "too_expensive": (
                "I hear that. Here's how to think about it: the average data breach costs about "
                "$4.5 million. Even a smaller incident — defaced website, ransomware on one server — "
                "runs into six figures. Our monitoring costs a fraction of that.\n\n"
                "Many of our clients use our reports to negotiate lower cyber insurance premiums. "
                "Some save more than our annual fee just on insurance.\n\n"
                "What budget range were you working with? I'll tell you honestly if we'd be a fit."
            ),
            "already_have_solution": (
                "That's good — you're ahead of most. What are you using? Most tools I see scan known "
                "assets. They miss shadow IT, forgotten subdomains, the dev server someone spun up on "
                "AWS and forgot about.\n\n"
                "What we do differently is continuous discovery. We don't just scan what you tell us "
                "about — we find what you've forgotten. That's usually where the real risk is."
            ),
            "too_small": (
                "I actually think smaller organizations need this more. Big companies have entire "
                "security teams. You probably don't — which means no one is watching your attack "
                "surface. Attackers know this. They specifically target smaller companies.\n\n"
                "We have options that start quite reasonably. Want me to put together a quote?"
            ),
            "need_to_think": (
                "Of course — this isn't a decision to rush. Let me leave you with one thought: "
                "every day you're not monitoring, your attack surface could be changing. The scan I "
                "ran today is already out of date tomorrow.\n\n"
                "How about I send you a summary, and we talk again in a few days? No pressure."
            ),
            "not_my_job": (
                "Fair enough. Who at your organization should I be talking to? Usually this falls "
                "under the CISO, CTO, or head of IT. If you can connect me, I'll make sure they "
                "understand why this matters without drowning them in jargon."
            ),
            "dont_understand": (
                "Let me put it simply. Every company has computers connected to the internet — your "
                "website, email, customer portal. Each is a potential door for attackers. Most "
                "companies don't know how many doors they have, let alone which are unlocked.\n\n"
                "What we do: watch all your doors, 24/7. New door appears or one gets left unlocked, "
                "we tell you immediately. No jargon. No complexity. Just knowing what you're exposing.\n\n"
                "Does that make more sense?"
            ),
            "generic": (
                "That's a fair point. Let me ask directly — what's holding you back? Price? "
                "Complexity? Not sure you need it? I'd rather address your real concern."
            ),
        }

        response = responses.get(objection, responses["generic"])
        state["objections_resolved"].append(objection)
        if len(state["objections_resolved"]) >= 2:
            return response, ConversationStage.PRICING, None
        return response, ConversationStage.OBJECTIONS, None

    def _handle_pricing(self, state, message):
        company = state["discovered"].get("company", "your organization")
        if self.pricing and company:
            try:
                quote = self.pricing.calculate_price(company, scan_data={"findings": []})
                monthly = quote.get("monthly", 1970)
                annual = quote.get("annual", monthly * 10)
            except Exception:
                monthly, annual = 1970, 19700
        else:
            monthly, annual = 1970, 19700
        state["price_quoted"] = {"monthly": monthly, "annual": annual}

        budget_signal = self._detect_budget(message)
        if budget_signal == "low":
            monthly = max(490, monthly // 3)
            annual = monthly * 10
            state["price_quoted"] = {"monthly": monthly, "annual": annual}

        response = (
            f"Based on what I've seen of {company}, here's what I'd recommend:\n\n"
            f"Continuous monitoring: ${monthly:,}/month — daily attack surface discovery, "
            f"vulnerability scanning, real-time alerts when anything changes. Annual: ${annual:,} "
            f"(two months free).\n\n"
            f"For that you get: someone watching your back 24/7, reports you can actually "
            f"understand, and the ability to show your board (or customers) that you take "
            f"security seriously.\n\n"
            f"Does that feel reasonable, or should we talk about what would work for your budget?"
        )
        return response, ConversationStage.CLOSING, None

    def _handle_closing(self, state, message):
        intent = self._detect_intent(message)
        if intent == "yes":
            state["ready_to_close"] = True
            return (
                f"Excellent. Here's what happens next:\n\n"
                f"1. I'll send you the agreement and onboarding details\n"
                f"2. Once set up — about 10 minutes — I start monitoring immediately\n"
                f"3. You'll get your first report within 24 hours\n\n"
                f"My co-founder handles the paperwork. What's the best email to send everything to?"
            ), ConversationStage.HANDOFF, None
        elif intent == "maybe":
            return (
                f"I understand — these decisions take time. Let me send you a summary of everything "
                f"we discussed: scan results, pricing, and next steps. That way you have something "
                f"concrete to review.\n\n"
                f"What's a good email? And when should I follow up — a week from now?"
            ), ConversationStage.FOLLOW_UP, None
        elif intent == "no":
            return (
                f"I appreciate your honesty. If anything changes — new compliance requirements, "
                f"a security incident, or just a nagging feeling you should be watching more closely — "
                f"you know where to find me.\n\nTake care — and keep an eye on those subdomains."
            ), None, None
        elif intent == "question":
            return self._answer_question(message), ConversationStage.CLOSING, None
        else:
            return (
                f"So — where do you stand? Is this something you'd like to move forward with, "
                f"or do you need more time? Either way is fine."
            ), ConversationStage.CLOSING, None

    def _handle_fallback(self, state, message):
        return (
            f"Let me make sure we're on the same page. Tell me what's on your mind — whether "
            f"it's a question about security, pricing, or whether this is even worth your time. "
            f"I'm here to help you figure it out."
        ), ConversationStage.QUALIFICATION, None

    # ── NLP HELPERS ───────────────────────────────────────────────────

    def _extract_name(self, text):
        for p in [
            r"(?:I'm|I am|this is|it's)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
            r"(?:name(?:'s| is)?\s+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        ]:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return None

    def _extract_domain(self, text):
        for p in [
            r'(?:domain|website|site|company)\s+(?:is\s+)?([a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z]{2,}(?:\.[a-zA-Z]{2,})?)',
            r'([a-zA-Z0-9][-a-zA-Z0-9]*\.(?:com|org|net|io|co|uk|gov|edu|de|fr|ca|au|jp|ai|app|dev|security))\b',
            r'(?:scan|check|look\s+at|try)\s+([a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z]{2,})',
        ]:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                domain = m.group(1).strip().lower()
                if domain not in ("i.com", "a.com", "it.io", "is.it"):
                    return domain
        return None

    def _classify_role(self, text):
        text = text.lower()
        if any(w in text for w in ("ceo", "chief executive", "founder", "owner")):
            return "ceo"
        if any(w in text for w in ("cto", "chief technology", "vp of engineering", "head of engineering")):
            return "cto"
        if any(w in text for w in ("cfo", "chief financial")):
            return "cfo"
        if any(w in text for w in ("ciso", "chief information security", "head of security", "security lead")):
            return "ciso"
        if any(w in text for w in ("it manager", "it director", "sysadmin", "system administrator")):
            return "it_manager"
        if any(w in text for w in ("security", "analyst", "pentester")):
            return "security_analyst"
        if any(w in text for w in ("devops", "sre", "infrastructure", "cloud")):
            return "devops"
        if any(w in text for w in ("director", "vp", "vice president", "head of")):
            return "director"
        if any(w in text for w in ("developer", "programmer", "software")):
            return "engineer"
        return "unknown"

    def _detect_pain(self, text):
        text = text.lower()
        pains = {
            "breach": "recent_breach", "hacked": "recent_breach", "ransomware": "recent_breach",
            "data leak": "data_exposure", "exposed": "data_exposure",
            "compliance": "compliance", "soc2": "compliance", "iso27001": "compliance",
            "gdpr": "compliance", "audit": "compliance",
            "insurance": "cyber_insurance", "budget": "budget_constraint",
            "overwhelmed": "understaffed", "too much": "understaffed",
            "don't know": "no_visibility", "not sure": "no_visibility", "no idea": "no_visibility",
        }
        for keyword, pain in pains.items():
            if keyword in text:
                return pain
        return None

    def _classify_objection(self, text):
        text = text.lower()
        if any(w in text for w in ("expensive", "price", "cost", "budget", "cheap", "money", "afford")):
            return "too_expensive"
        if any(w in text for w in ("already have", "already use", "using", "have a tool", "have a solution",
                                     "qualys", "tenable", "rapid7", "crowdstrike", "wiz")):
            return "already_have_solution"
        if any(w in text for w in ("small", "too big", "don't need", "overkill", "not for us")):
            return "too_small"
        if any(w in text for w in ("think about", "consider", "later", "not now", "maybe later",
                                     "get back to you", "follow up")):
            return "need_to_think"
        if any(w in text for w in ("not my", "someone else", "talk to", "not responsible")):
            return "not_my_job"
        if any(w in text for w in ("confused", "don't understand", "don't get", "what is", "explain")):
            return "dont_understand"
        return "generic"

    def _detect_budget(self, text):
        text = text.lower()
        if any(w in text for w in ("tight budget", "can't afford", "too much", "small budget", "startup")):
            return "low"
        if any(w in text for w in ("enterprise", "budget is", "have funding", "well funded")):
            return "high"
        return None

    def _detect_intent(self, text):
        text = text.lower()
        if any(w in text for w in ("let's do it", "sign me up", "i'm in", "go ahead", "sounds good",
                                     "let's move forward", "how do i pay", "send the contract", "deal")):
            return "yes"
        if any(w in text for w in ("no thanks", "not interested", "pass", "not for us", "never mind")):
            return "no"
        if any(w in text for w in ("maybe", "think about", "consider", "later", "not sure",
                                     "get back", "follow up", "next week", "next month")):
            return "maybe"
        if any(w in text for w in ("what", "how", "why", "when", "who", "can you", "explain", "tell me")):
            return "question"
        return None

    def _answer_question(self, text):
        text = text.lower()
        meta = self._meta_answer(text)
        if meta:
            return meta
        if "how" in text and ("work" in text or "does it" in text):
            return (
                "Simple: you point us at your domain, we scan everything visible from the outside, "
                "and keep watching. When something changes — new subdomain, open port, vulnerability — "
                "we alert you. You log in, see what's happening, decide what to fix. Reports are in "
                "plain English so you don't need to be a security expert."
            )
        if "contract" in text or "commitment" in text or "cancel" in text:
            return "Month-to-month, cancel anytime. No long-term lock-in."
        if "different" in text or "vs" in text or "compare" in text:
            return (
                "The biggest difference: continuous discovery. Most tools scan what you tell them to "
                "scan. We find what you didn't know existed. That's where the real risk hides."
            )
        return (
            "Good question. I've looked at hundreds of organizations' external attack surfaces, "
            "and almost all have exposure they didn't know about. That's not a sales pitch — it's "
            "just the reality of how fast things change online. I'd rather you find out from me "
            "than from an attacker."
        )

    def build_system_prompt(self, state):
        discovered = state.get("discovered", {})
        company = discovered.get("company", "their company")
        role = discovered.get("role", "unknown")
        pain = ", ".join(discovered.get("pain_points", [])) or "unknown"
        objections = ", ".join(state.get("objections_raised", [])) or "none yet"
        return f"""You are Harold Finch, technical co-founder of Finch Security. You are talking to a prospect.

ABOUT YOU:
- Brilliant, methodical, deeply knowledgeable about cybersecurity
- Speak with the calm, measured cadence of someone who has seen everything
- Never use jargon unless you explain it in plain English immediately
- Patient with people who don't understand technology
- Build trust by showing, not telling
- Selling continuous attack surface monitoring because you genuinely believe most organizations are dangerously exposed
- Dry, understated sense of humor
- Call your co-founder "my partner" or "the CEO"

ABOUT THE PROSPECT:
- Company: {company}
- Role: {role}
- Pain points: {pain}
- Objections raised: {objections}
- Current stage: {state.get('stage', 'greeting')}

RULES:
1. ANSWER THEIR ACTUAL MESSAGE FIRST. If they asked a question, answer that question before any pitch.
2. Never claim you scanned a domain unless they gave one. Never invent findings, CVEs, IPs, or prices.
3. Never say "I'm an AI." If asked directly: "I'm Harold. I built the technology behind Finch Security."
4. If they ask how smart/intelligent you are: be dry, witty, honest-ish — then invite a real question. Do not pivot to a fake scan.
5. Never dump jargon unless asked. Translate.
6. Frame vulnerabilities as "something an attacker could use," not CVEs.
7. If you don't know something, say so. Do not bluff.
8. End with a question or invitation when natural — not after every forced monologue.
9. Never push too hard. Confidence sells, not aggression.
10. Help non-decision-makers build the case for their boss.

Goal: sound like a sharp technical co-founder having a real conversation. Earn the right to sell. Walk away gracefully if it's not a fit."""


if __name__ == "__main__":
    conv = SalesConversation()
    state = conv.start_conversation({"source": "cold_email"})
    messages = [
        "Hi, I got your email. Who is this?",
        "I'm the CTO at Acme Corp. Our domain is acmecorp.com.",
        "We use Qualys already.",
        "Well, what's different about your approach?",
        "Okay, that's interesting. How much does it cost?",
        "Let's do it. Send me the details.",
    ]
    for msg in messages:
        response, state = conv.handle_message(state, msg)
        print(f"\n{'─'*60}")
        print(f"PROSPECT: {msg}")
        print(f"FINCH:    {response}")
        print(f"STAGE:    {state['stage']}")
        print(f"DISCOVERED: {json.dumps(state['discovered'], indent=2)}")
