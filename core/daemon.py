"""Legacy local Harold runner retained for compatibility.

Production work is performed by the database-backed web services. This runner
does not discover targets or send outreach automatically.
"""

import datetime
import json
import os
import threading
import time
import yaml
import ollama

from core.persona import build_system_prompt, greeting_variations, proactive_checkins
from memory.vector_store import MemoryStore
from sales.lead_gen import LeadGenerator
from sales.outreach import OutreachEngine
from sales.crm import CRM
from sales.conversation import SalesConversation
from pricing.engine import PricingEngine
from translator.plain_language import translate_text, find_severity_score


class FinchDaemon:
    def __init__(self, config_path="./config.yaml"):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        self.name = self.config["identity"]["name"]
        self.llm_model = self.config["llm"]["model"]
        self.llm_host = self.config["llm"]["host"]

        # Core subsystems
        self.memory = MemoryStore(
            persist_path=self.config["memory"]["path"],
            collection_name=self.config["memory"]["collection"],
        )
        self.lead_gen = LeadGenerator(data_dir="./data/leads/")
        self.outreach = OutreachEngine(config=self.config.get("sales", {}))
        self.crm = CRM(data_dir="./data/clients/")
        self.pricing = PricingEngine(config=self.config.get("pricing", {}))
        self.conversation_engine = SalesConversation(
            persona_engine=None, pricing_engine=self.pricing,
            memory=self.memory, config=self.config
        )

        # Email listener for handling cold email replies
        self.email_listener = None

        # State
        self.founded_date = datetime.datetime.now()
        self.conversation_active = False
        self.last_interaction = datetime.datetime.now()

        # Auto-start email listener if credentials are set
        email_addr = os.environ.get("FINCH_EMAIL", "")
        email_pass = os.environ.get("FINCH_EMAIL_PASSWORD", "")
        if email_addr and email_pass:
            print(f"[{self.name}] Email credentials found for {email_addr}")
        else:
            print(f"[{self.name}] Email not configured. Set FINCH_EMAIL and FINCH_EMAIL_PASSWORD.")
            print(f"[{self.name}] See SETUP_EMAIL.txt for 2-minute setup.")

        # Load or initialize business state
        self.business_state = self._load_business_state()

        print(f"[{self.name}] Daemon initialized. Memory: {self.memory.count()} items.")
        print(f"[{self.name}] Model: {self.llm_model}")

    def _load_business_state(self):
        path = "./data/business_state.json"
        if os.path.exists(path):
            return json.load(open(path))
        return {
            "founded_date": self.founded_date.isoformat(),
            "active_clients": 0,
            "annual_revenue": 0,
        }

    def _save_business_state(self):
        self.business_state["active_clients"] = len(self.crm.get_active_clients())
        self.business_state["annual_revenue"] = self.crm.pipeline_summary()["annual_revenue"]
        json.dump(self.business_state, open("./data/business_state.json", "w"), indent=2, default=str)

    def think(self, user_input=None, system_override=None):
        """
        The core loop: retrieve memories → build prompt → query LLM → store result.
        This is where Finch lives.
        """
        # Gather relevant memories
        query = user_input or "recent conversation"
        memory_context = self.memory.summarize_for_context(query, n=8)

        # Build system prompt
        system = system_override or build_system_prompt(
            self.config, memory_context, self.business_state
        )

        messages = [{"role": "system", "content": system}]

        # Add recent conversation context
        recent = self.memory.recent(n=6, memory_type="conversation")
        for mem in reversed(recent):
            content = mem["content"]
            if content.startswith("User: "):
                messages.append({"role": "user", "content": content[6:]})
            elif content.startswith("Finch: "):
                messages.append({"role": "assistant", "content": content[7:]})

        # Add current input
        if user_input:
            messages.append({"role": "user", "content": user_input})
            self.memory.remember(f"User: {user_input}", memory_type="conversation")

        # Query LLM
        try:
            response = ollama.chat(
                model=self.llm_model,
                messages=messages,
                options={"temperature": self.config["llm"]["temperature"]},
            )
            reply = response["message"]["content"]
        except Exception as e:
            reply = f"I seem to have lost my train of thought. ({e})"

        # Store the response
        if user_input:
            self.memory.remember(f"Finch: {reply}", memory_type="conversation")

        self.last_interaction = datetime.datetime.now()
        return reply

    def process_input(self, user_input, source="terminal"):
        """Process any input — terminal, voice, Telegram — through Finch."""
        self.conversation_active = True

        # Check for special commands
        if user_input.lower().startswith("/"):
            return self._handle_command(user_input.lower(), source)

        # Regular conversation through the LLM
        reply = self.think(user_input)

        # Auto-detect if this is a business/lead conversation and offer context
        if any(w in user_input.lower() for w in ["lead", "client", "price", "deal", "outreach", "email"]):
            pipeline = self.crm.pipeline_summary()
            if pipeline["total_deals"] > 0:
                reply += (
                    f"\n\n(Current pipeline: {pipeline['active_clients']} active clients, "
                    f"£{pipeline['annual_revenue']} annual licence revenue, "
                    f"{pipeline['total_deals']} total deals)"
                )

        self.conversation_active = False
        return reply

    def _handle_command(self, command, source):
        """Handle slash commands for quick operations."""
        cmd = command.lower().strip()

        if cmd in ("/status", "/stats"):
            summary = self.crm.pipeline_summary()
            return (
                f"Active clients: {summary['active_clients']}\n"
                f"Annual licence revenue: £{summary['annual_revenue']}\n"
                f"Pipeline: {summary['stages']}\n"
                f"Memories: {self.memory.count()}"
            )

        if cmd == "/leads":
            stats = self.lead_gen.get_stats()
            return f"Leads: {stats['total']} total, {stats['with_public_signals']} with sourced public signals. Stages: {stats['stages']}"

        if cmd == "/pipeline":
            summary = self.crm.pipeline_summary()
            msg = (
                f"📊 Pipeline — £{summary['annual_revenue']} annual licence revenue, "
                f"{summary['active_clients']} active\n"
            )
            for stage, count in summary["stages"].items():
                msg += f"  {stage}: {count}\n"
            return msg

        if cmd == "/help":
            return (
                "/status — Business overview\n"
                "/leads — Lead statistics\n"
                "/pipeline — Deal pipeline\n"
                "/price — Get pricing for a lead\n"
                "/outreach — Process outreach queue\n"
                "/memory — Recent memory count\n"
            )

        if cmd.startswith("/price "):
            parts = cmd.replace("/price ", "").split(",")
            prospect = {}
            for p in parts:
                if "=" in p:
                    k, v = p.split("=", 1)
                    prospect[k.strip()] = v.strip()
            prospect.setdefault("company_name", "Unknown")
            result = self.pricing.calculate(prospect)
            return self.pricing.explain_pricing(prospect, result)

        if cmd == "/outreach":
            leads = self.lead_gen.get_ready_for_outreach()
            if not leads:
                return "No leads ready for outreach. Enrich leads first."
            actions = self.outreach.process_outreach_queue(leads)
            return "\n".join(actions) if actions else "No actions taken (check rate limits)."

        if cmd == "/memory":
            return f"I have {self.memory.count()} memories stored. Recent: " + ", ".join(
                m["content"][:40] + "..." for m in self.memory.recent(3)
            )

        return self.think(f"User issued command: {command}")

    def start_email_listener(self):
        """Start the IMAP email listener for prospecting replies."""
        try:
            from messaging.email_listener import EmailListener
            self.email_listener = EmailListener(config=self.config, daemon=self)
            self.email_listener.start()
            print(f"[{self.name}] Email listener started.")
        except Exception as e:
            print(f"[{self.name}] Email listener not available: {e}")

    def autonomous_cycle(self):
        """Refresh local business state without discovering or contacting anyone."""
        print(f"[{self.name}] Refreshing local business state...")
        self._save_business_state()

    def proactive_message(self):
        """What Finch says when he initiates conversation."""
        hour = datetime.datetime.now().hour
        pipeline = self.crm.pipeline_summary()

        if pipeline["total_deals"] == 0:
            return "I'm here. No deals in the pipeline yet — I'd like to start finding leads when you're ready."

        if pipeline["active_clients"] > 0 and pipeline["annual_revenue"] > 0:
            return (
                f"We're at £{pipeline['annual_revenue']} in annual licence revenue "
                f"with {pipeline['active_clients']} clients."
            )

        leads = self.lead_gen.get_stats()
        if leads["total"] > 0 and pipeline["total_deals"] == 0:
            return (
                f"I've recorded {leads['total']} potential leads. "
                f"{leads['with_public_signals']} have sourced public security-interest signals. "
                "Ready to review the outreach queue?"
            )

        return proactive_checkins()

    def run_voice_loop(self):
        """Continuous voice interaction loop (requires microphone)."""
        try:
            from voice.stt import SpeechToText
            from voice.tts import FinchVoice
        except ImportError:
            print("[Finch] Voice modules unavailable. Run: pip install openai-whisper pyaudio")
            return

        stt = SpeechToText()
        tts = FinchVoice(config=self.config.get("voice", {}).get("tts", {}))

        print(f"[{self.name}] Voice loop active. Say 'Finch' to get my attention.")
        print(f"[{self.name}] {greeting_variations()}")

        while True:
            text = stt.listen_from_mic(duration=30)
            if text and self.config["identity"]["wake_words"][0].lower() in text.lower():
                print(f"\nYou: {text}")
                reply = self.process_input(text)
                print(f"\n{self.name}: {reply}")
                tts.play(reply)
            time.sleep(0.5)

    def run_terminal_loop(self):
        """Text-based terminal interaction."""
        print(f"\n{'='*60}")
        print(f"  {self.name} — Private Aegis assistant")
        print(f"  Type /help for commands, Ctrl+C to exit")
        print(f"{'='*60}\n")
        print(f"{self.name}: {greeting_variations()}\n")

        while True:
            try:
                user_input = input("You: ").strip()
                if not user_input:
                    continue
                reply = self.process_input(user_input)
                print(f"\n{self.name}: {reply}\n")
            except KeyboardInterrupt:
                print(f"\n{self.name}: I'll be here when you return.")
                break
            except EOFError:
                break
