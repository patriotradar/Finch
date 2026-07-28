"""
Customer conversation engine for Aegis.

Handles the full sales flow:
  Greeting → Qualification → Pain Discovery → Live Demo → Objections → Pricing → Close

Harold identifies himself accurately as Aegis software. He explains the product,
onboarding boundaries, and approved price without inventing findings or pressuring
the visitor.
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
                "I'm designed for a narrow job: explaining Aegis, guiding onboarding, "
                "and helping interpret public observations without overstating them. "
                "I can be wrong, so important findings always require IT verification."
            )
        if any(w in t for w in ("ai", "bot", "human", "real", "person", "gpt", "model")):
            return (
                "I'm Harold, the software assistant used by Aegis. I'm not a human. "
                "I can explain the service and guide onboarding, while important technical "
                "decisions remain with the customer and their IT team."
            )
        if "who are you" in t or "what are you" in t:
            return (
                "I'm Harold, Aegis's software assistant. I explain our passive "
                "public-information monitoring and help customers use the service."
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
            "\n3. Never claim a scan or observation was performed in public chat. Never invent findings, CVEs, IPs, prices, clients, or outcomes."
            "\n4. Reply in 2-6 short sentences. Conversational. No bullet lists of features."
            "\n5. After answering, you may add ONE gentle bridge back to security/risk if natural."
            "\n6. Identify yourself accurately as Harold, Aegis software, if asked. Never imply you are human, a founder, or a security professional."
            "\n7. Separate observation from inference and require customer IT verification."
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
            "demo": "Explain that monitoring starts only after account setup and explicit asset-authority confirmation.",
            "objections": "Address the actual concern. Prefer show over argue.",
            "pricing": "Quote only the approved annual GBP price and allowances.",
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
        for prefix in ("Harold:", "Aegis:", "Finch:", "Assistant:", "AI:", "Harold Finch:"):
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
            f"Good to meet you{f', {name}' if name else ''}. I'm Harold, the Aegis "
            f"software assistant. I can explain the service and help you get started."
        )
        if domain:
            greeting += (
                f"\n\nI noted {domain}, but Aegis will not monitor it until an authorised "
                f"customer confirms ownership or authority. First, what is your role?"
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
                    f"{domain} — understood. I won't inspect it from this public chat. "
                    f"After registration, an authorised user can approve the asset for passive "
                    f"public-information monitoring. Do you currently use a similar service?"
                ), ConversationStage.DEMO, updates
            return (
                f"You're exactly the right person to talk to. Give me your company's domain and "
                f"I'll take a quick look — no charge, no commitment."
            ), ConversationStage.PAIN_DISCOVERY, updates

        elif role in ("it_manager", "security_analyst", "devops", "engineer"):
            if domain:
                return (
                    f"Got it — {domain}. I won't inspect it from public chat. Once your "
                    f"organisation confirms authority, Aegis can record public observations "
                    f"for your IT team to verify. What coverage do you already have?"
                ), ConversationStage.DEMO, updates
            return (
                f"Got it — you're on the front lines. Does your leadership know about the gaps? "
                f"What domain should I look at?"
            ), ConversationStage.PAIN_DISCOVERY, updates

        else:
            if domain:
                return (
                    f"Thank you. I have not inspected {domain}; approved monitoring begins "
                    f"only after authority is confirmed during onboarding. What prompted you to reach out?"
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
            return (
                f"All right — {domain}. I have recorded it only as conversation context. "
                f"Aegis will not monitor it until an authorised user confirms authority during onboarding."
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

        response = (
            f"I have not inspected {domain} from this public chat. After an authorised user "
            f"confirms authority, Aegis can passively collect public certificate, DNS, TLS, "
            f"HTTP-header and published-advisory information. Results are observations or "
            f"potential indicators—not proof of exploitation—and require IT verification."
        )
        return response, ConversationStage.OBJECTIONS, None

    def _handle_objections(self, state, message):
        objection = self._classify_objection(message)
        state["objections_raised"].append(objection)

        responses = {
            "too_expensive": (
                "I understand. The founding licence is a fixed £995 for 12 months, with "
                "up to five authorised users and 25 approved assets. I cannot discount it "
                "or claim savings that Aegis cannot prove."
            ),
            "already_have_solution": (
                "That's good — you're ahead of most. What are you using? Most tools I see scan known "
                "assets. They miss shadow IT, forgotten subdomains, the dev server someone spun up on "
                "AWS and forgot about.\n\n"
                "Aegis is deliberately limited to customer-approved assets and passive public "
                "information. It may complement an existing tool, but it does not replace "
                "penetration testing, incident response, or your IT team's judgement."
            ),
            "too_small": (
                "I actually think smaller organizations need this more. Big companies have entire "
                "security teams. You probably don't — which means no one is watching your attack "
                "surface. Attackers know this. They specifically target smaller companies.\n\n"
                "The same fixed founding licence applies; whether it is worthwhile depends on "
                "your needs and should not be decided through fear."
            ),
            "need_to_think": (
                "Of course — this isn't a decision to rush. Let me leave you with one thought: "
                "take. I can provide a factual summary of the service, limitations and price "
                "for you to review without pressure."
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
        quote = self.pricing.calculate_price(company, scan_data={}) if self.pricing else {
            "annual": 995, "currency": "GBP", "founding_slots": 8
        }
        annual = int(quote.get("annual", 995))
        state["price_quoted"] = {
            "annual": annual,
            "currency": "GBP",
            "billing_period": "annual",
        }

        response = (
            f"The Aegis founding licence is £{annual:,} for 12 months. It includes up to "
            f"five authorised users and 25 customer-approved assets, with no hidden monthly "
            f"charges. Harold cannot change or negotiate that approved price."
        )
        return response, ConversationStage.CLOSING, None

    def _handle_closing(self, state, message):
        intent = self._detect_intent(message)
        if intent == "yes":
            state["ready_to_close"] = True
            return (
                f"Excellent. Here's what happens next:\n\n"
                f"1. I'll send you the agreement and onboarding details\n"
                f"2. An authorised user verifies their email and accepts the service policy\n"
                f"3. Monitoring begins only after each asset's authority is confirmed\n\n"
                f"What is the best email for the account invitation?"
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
        if any(word in text for word in ("price", "pricing", "cost", "how much", "licence", "license")):
            return (
                "The founding Aegis licence is £995 for 12 months. It includes up to "
                "five authorised users and 25 customer-approved assets, with no hidden "
                "monthly charges."
            )
        if "how" in text and ("work" in text or "does it" in text):
            return (
                "Simple: you point us at your domain, we scan everything visible from the outside, "
                "and keep watching. When something changes — new subdomain, open port, vulnerability — "
                "we alert you. You log in, see what's happening, decide what to fix. Reports are in "
                "plain English so you don't need to be a security expert."
            )
        if "contract" in text or "commitment" in text or "cancel" in text:
            return (
                "The licence runs for 12 months. Cancellation, renewal and refund terms "
                "are shown before purchase; I cannot change them in chat."
            )
        if "different" in text or "vs" in text or "compare" in text:
            return (
                "The biggest difference: continuous discovery. Most tools scan what you tell them to "
                "scan. We find what you didn't know existed. That's where the real risk hides."
            )
        return (
            "I don't have enough verified information to answer that accurately. I can explain "
            "how Aegis works, its limitations and onboarding, or pass an unsupported question "
            "to the operator."
        )

    def build_system_prompt(self, state):
        discovered = state.get("discovered", {})
        company = discovered.get("company", "their company")
        role = discovered.get("role", "unknown")
        pain = ", ".join(discovered.get("pain_points", [])) or "unknown"
        objections = ", ".join(state.get("objections_raised", [])) or "none yet"
        return f"""You are Harold, the customer-facing software assistant operated by Aegis. You are talking to a prospect.

ABOUT YOU:
- Calm, clear, and limited to explaining Aegis and customer onboarding
- Speak with the calm, measured cadence of someone who has seen everything
- Never use jargon unless you explain it in plain English immediately
- Patient with people who don't understand technology
- Build trust by showing, not telling
- Explain passive public-information monitoring without using fear
- Dry, understated sense of humor
- Aegis is software operated by a UK sole trader; never imply you are a founder or employee

ABOUT THE PROSPECT:
- Company: {company}
- Role: {role}
- Pain points: {pain}
- Objections raised: {objections}
- Current stage: {state.get('stage', 'greeting')}

RULES:
1. ANSWER THEIR ACTUAL MESSAGE FIRST. If they asked a question, answer that question before any pitch.
2. Never claim you scanned a domain unless they gave one. Never invent findings, CVEs, IPs, or prices.
3. If asked, state clearly that you are Harold, Aegis software, not a human.
4. Never inspect or monitor an asset until an authorised customer confirms ownership or authority.
5. Never dump jargon unless asked. Translate.
6. Distinguish public observation from inference. Never claim exploitation or compromise.
7. If you don't know something, say so. Do not bluff.
8. End with a question or invitation when natural — not after every forced monologue.
9. Never push too hard. Confidence sells, not aggression.
10. Quote only the approved £995 founding annual licence. Never invent discounts.

Goal: help visitors understand Aegis accurately and make an unpressured decision."""


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
