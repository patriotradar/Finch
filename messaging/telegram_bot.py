"""
Telegram integration — Finch talks to both you AND your prospects.

Phone-first design. Everything works from Telegram:
- Admin commands: /status, /leads, /pipeline, /docs, /send
- Forward documents to Finch from your phone → he stores & can email them
- Prospects who message the bot get the full SalesConversation flow

Architecture:
  You (admin) ←→ Finch (co-founder chat, commands, docs)
  Prospect ←→ SalesConversation engine (autonomous, Finch handles it)
"""

import os
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from sales.conversation import SalesConversation
from memory.vector_store import MemoryStore
from messaging.telegram_docs import FinchDocs
import yaml


class FinchTelegram:
    def __init__(self, token=None, daemon=None, config=None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.daemon = daemon
        self.config = config or self._load_config()
        self.app = None

        self.admin_id = int(os.environ.get("TELEGRAM_ADMIN_ID", "0"))
        self.memory = MemoryStore(self.config.get("memory", {}))
        self.conversation_engine = SalesConversation(
            persona_engine=None, pricing_engine=None,
            memory=self.memory, config=self.config,
        )
        self.docs = FinchDocs(crm=None)
        self.prospect_sessions = {}

        # Pending handoffs: {prospect_email: deal_info}
        self.pending_handoffs = {}

    @staticmethod
    def _load_config():
        config_path = Path(__file__).parent.parent / "config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                return yaml.safe_load(f)
        return {}

    # ── ADMIN COMMANDS ────────────────────────────────────────────────

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if self.admin_id and user_id != self.admin_id:
            await self._handle_prospect_message(update, context)
            return
        await update.message.reply_text(
            "I'm here. What do you need?\n\n"
            "/status — Pipeline and annual revenue\n"
            "/leads — Lead stats\n"
            "/pipeline — Deal pipeline\n"
            "/docs — Stored documents (contracts, invoices)\n"
            "/send <doc_id> to <email> — Email a document to a prospect\n"
            "/handoffs — Pending deals waiting for paperwork"
        )

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            return await self._handle_prospect_message(update, context)
        if self.daemon and hasattr(self.daemon, 'crm'):
            summary = self.daemon.crm.pipeline_summary()
            await update.message.reply_text(
                f"💰 Annual licence revenue: £{summary.get('annual_revenue', 0):,}\n"
                f"👥 Active clients: {summary['active_clients']}\n"
                f"📋 Deals in pipeline: {summary['total_deals']}\n"
                f"📊 Stages: {summary['stages']}"
            )
        else:
            await update.message.reply_text("Daemon not connected. Start with `python main.py --daemon`.")

    async def cmd_leads(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            return await self._handle_prospect_message(update, context)
        if self.daemon and hasattr(self.daemon, 'lead_gen'):
            stats = self.daemon.lead_gen.get_stats()
            await update.message.reply_text(
                f"🔍 Total leads: {stats['total']}\n"
                f"Public security signals: {stats['with_public_signals']}\n"
                f"📬 Outreached: {stats['stages'].get('outreached', 0)}\n"
                f"💬 In conversation: {stats['stages'].get('in_conversation', 0)}\n"
                f"✅ Closed: {stats['stages'].get('closed', 0)}"
            )
        else:
            await update.message.reply_text("Lead engine not available.")

    async def cmd_pipeline(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            return await self._handle_prospect_message(update, context)
        if self.daemon:
            summary = self.daemon.crm.pipeline_summary()
            msg = "📊 Pipeline\n\n"
            for stage, count in summary["stages"].items():
                bar = "█" * min(count, 20)
                msg += f"{stage}: {bar} {count}\n"
            if summary.get("deals"):
                msg += "\nActive deals:\n"
                for deal in summary["deals"][:5]:
                    msg += f"  • {deal.get('client_name', 'Unknown')} — £{deal.get('annual', 0):,}/year ({deal.get('stage', '?')})\n"
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text("CRM not connected.")

    async def cmd_docs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List stored documents — contracts, invoices, onboarding files."""
        if not self._is_admin(update):
            return await self._handle_prospect_message(update, context)

        args = context.args
        tag = args[0] if args else None
        docs = self.docs.list_documents(tag=tag)

        if not docs:
            await update.message.reply_text("No documents stored yet. Forward a file to me and I'll keep it safe.")
            return

        msg = f"📁 Documents" + (f" tagged '{tag}'" if tag else "") + f" ({len(docs)} total):\n\n"
        for doc in docs[-15:]:
            tags = ", ".join(doc.get("tags", ["untagged"]))
            client = f" → {doc['linked_client']}" if doc.get("linked_client") else ""
            msg += f"#{doc['id']}: {doc['original_name']} [{tags}]{client}\n"

        msg += "\nForward any file here and I'll store it. Use /tag <id> <tag> to organize."
        await update.message.reply_text(msg)

    async def cmd_send(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send a stored document to a prospect via email.
        Usage: /send 3 to john@acmecorp.com
        Or:    /send contract_acmecorp to john@acmecorp.com (by tag)
        """
        if not self._is_admin(update):
            return await self._handle_prospect_message(update, context)

        args = context.args
        if len(args) < 3 or "to" not in [a.lower() for a in args]:
            await update.message.reply_text(
                "Usage: /send <doc_id> to <email>\n"
                "Example: /send 3 to john@acmecorp.com\n"
                "Or by tag: /send contract_acmecorp to john@acmecorp.com"
            )
            return

        to_idx = [a.lower() for a in args].index("to")
        identifier = " ".join(args[:to_idx])
        email = args[to_idx + 1] if to_idx + 1 < len(args) else None

        if not email or "@" not in email:
            await update.message.reply_text("Please provide a valid email address.")
            return

        # Find document by ID or tag
        doc = None
        try:
            doc_id = int(identifier)
            docs = self.docs.list_documents()
            for d in docs:
                if d["id"] == doc_id:
                    doc = d
                    break
        except ValueError:
            # Search by tag
            docs = self.docs.list_documents(tag=identifier)
            if docs:
                doc = docs[-1]  # Most recent with that tag

        if not doc:
            await update.message.reply_text(f"No document found for '{identifier}'. Use /docs to list what's stored.")
            return

        # Send via the daemon's outreach engine
        if self.daemon and hasattr(self.daemon, 'outreach'):
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            from email.mime.base import MIMEBase
            from email import encoders
            import smtplib, ssl

            from_addr = os.environ.get("FINCH_EMAIL")
            passwd = os.environ.get("FINCH_EMAIL_PASSWORD")

            if not from_addr or not passwd:
                await update.message.reply_text("Email not configured. Set FINCH_EMAIL and FINCH_EMAIL_PASSWORD.")
                return

            try:
                msg = MIMEMultipart()
                msg["From"] = f"Harold from Aegis <{from_addr}>"
                msg["To"] = email
                msg["Subject"] = f"Onboarding documents from Aegis"

                body = (
                    f"Hi,\n\n"
                    f"As promised, here's the paperwork to get started with Aegis.\n\n"
                    f"If you have any questions, reply here or reach me on Telegram.\n\n"
                    f"Best,\nHarold\nAegis"
                )
                msg.attach(MIMEText(body, "plain"))

                # Attach the document
                file_path = doc["path"]
                if os.path.exists(file_path):
                    with open(file_path, "rb") as f:
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(f.read())
                        encoders.encode_base64(part)
                        part.add_header("Content-Disposition", f'attachment; filename="{doc["original_name"]}"')
                        msg.attach(part)

                context_ssl = ssl.create_default_context()
                with smtplib.SMTP("smtp.gmail.com", 587) as s:
                    s.starttls(context=context_ssl)
                    s.login(from_addr, passwd)
                    s.sendmail(from_addr, email, msg.as_string())

                self.docs.tag_document(doc["id"], "sent")
                await update.message.reply_text(
                    f"✅ Sent '{doc['original_name']}' to {email}\n"
                    f"Document tagged as 'sent'."
                )
            except Exception as e:
                await update.message.reply_text(f"❌ Failed to send: {e}")
        else:
            await update.message.reply_text("Daemon with email config is required to send documents.")

    async def cmd_tag(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Tag a stored document: /tag 3 contract"""
        if not self._is_admin(update):
            return await self._handle_prospect_message(update, context)

        args = context.args
        if len(args) < 2:
            await update.message.reply_text("Usage: /tag <doc_id> <tag>\nExample: /tag 3 contract")
            return

        try:
            doc_id = int(args[0])
            tag = args[1].lower()
        except ValueError:
            await update.message.reply_text("First argument must be a document ID number.")
            return

        result = self.docs.tag_document(doc_id, tag)
        if result:
            await update.message.reply_text(f"✅ Document #{doc_id} tagged '{tag}'.")
        else:
            await update.message.reply_text(f"Document #{doc_id} not found.")

    async def cmd_handoffs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show pending deals waiting for paperwork."""
        if not self._is_admin(update):
            return await self._handle_prospect_message(update, context)

        if not self.pending_handoffs:
            await update.message.reply_text("No pending handoffs. When a deal closes, I'll alert you here.")
            return

        msg = "📋 Pending handoffs — these prospects are waiting for onboarding:\n\n"
        for email, info in self.pending_handoffs.items():
            msg += (
                f"🏢 {info.get('company', 'unknown')}\n"
                f"   Email: {email}\n"
                f"   Price: ${info.get('price', 0):,}/mo\n"
                f"   Forward me the contract and I'll send it.\n\n"
            )
        msg += "Use /docs to see stored documents. /send <id> to <email> to send one."
        await update.message.reply_text(msg)

    async def cmd_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            return
        reply = update.message.reply_to_message
        if reply and reply.from_user:
            pid = reply.from_user.id
            if pid in self.prospect_sessions:
                del self.prospect_sessions[pid]
                await update.message.reply_text(f"Conversation reset.")
            else:
                await update.message.reply_text("No active conversation with that user.")
        else:
            await update.message.reply_text("Reply to a prospect's message to reset their conversation.")

    # ── MESSAGE & DOCUMENT HANDLERS ───────────────────────────────────

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        is_admin = self.admin_id and user_id == self.admin_id

        if is_admin:
            user_text = update.message.text
            if self.daemon:
                response = self.daemon.process_input(user_text, source="telegram")
                await update.message.reply_text(response)
            else:
                await update.message.reply_text("Daemon isn't running. Start with `python main.py --daemon`.")
        else:
            await self._handle_prospect_message(update, context)

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle forwarded files — contracts, PDFs, invoices from your phone."""
        user_id = update.effective_user.id
        is_admin = self.admin_id and user_id == self.admin_id

        if not is_admin:
            await update.message.reply_text("I don't accept files from people I don't know. Are you a client?")
            return

        document = update.message.document
        if not document:
            await update.message.reply_text("I couldn't read that file. Try forwarding it again.")
            return

        file_name = document.file_name or "unnamed_document"
        await update.message.reply_text(f"📥 Receiving '{file_name}'...")

        # Download the file
        file_obj = await context.bot.get_file(document.file_id)
        tmp_path = f"/tmp/finch_tg_{user_id}_{file_name}"
        await file_obj.download_to_drive(tmp_path)

        # Store it
        entry = self.docs.receive_document(
            file_path=tmp_path,
            file_name=file_name,
            sender=str(user_id),
        )

        # Clean up temp file
        os.remove(tmp_path)

        # Auto-tag based on filename
        name_lower = file_name.lower()
        if "contract" in name_lower or "agreement" in name_lower:
            self.docs.tag_document(entry["id"], "contract")
        elif "invoice" in name_lower or "bill" in name_lower:
            self.docs.tag_document(entry["id"], "invoice")
        elif "onboard" in name_lower or "welcome" in name_lower:
            self.docs.tag_document(entry["id"], "onboarding")

        # Check if there's a pending handoff we can link this to
        reply_text = f"✅ Stored as document #{entry['id']}: {file_name}"
        if self.pending_handoffs:
            reply_text += "\n\nYou have pending handoffs. Reply with:\n"
            reply_text += f"  /send {entry['id']} to <their-email>"
        reply_text += f"\n  /tag {entry['id']} <label>"

        await update.message.reply_text(reply_text)

    async def _handle_prospect_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name or "there"
        message = update.message.text

        if user_id not in self.prospect_sessions:
            state = self.conversation_engine.start_conversation(
                prospect_info={
                    "source": "telegram",
                    "telegram_id": user_id,
                    "name": user_name,
                    "username": update.effective_user.username,
                }
            )
            self.prospect_sessions[user_id] = state
        else:
            state = self.prospect_sessions[user_id]

        if message.lower() in ("/reset", "/restart", "start over"):
            state = self.conversation_engine.start_conversation(
                prospect_info=state.get("prospect", {})
            )
            self.prospect_sessions[user_id] = state
            await update.message.reply_text("Starting fresh. What can I help with?")
            return

        response, state = self.conversation_engine.handle_message(state, message)
        self.prospect_sessions[user_id] = state

        # If deal closed, capture email and queue handoff
        if state.get("ready_to_close"):
            self.memory.remember(
                f"PROSPECT READY TO CLOSE: {user_name} (Telegram ID: {user_id}). "
                f"Discovered: {state['discovered']}",
                metadata={"type": "deal_closed", "prospect_id": str(user_id)}
            )

            # Try to extract their email from the last message
            email = self._extract_email(message)
            if email:
                self.pending_handoffs[email] = {
                    "company": state["discovered"].get("company", "unknown"),
                    "price": state.get("price_quoted", {}).get("monthly", 0),
                    "contact": user_name,
                    "telegram_id": user_id,
                    "discovered": state["discovered"],
                }

            if self.admin_id:
                alert = (
                    f"🔔 Deal ready!\n\n"
                    f"{user_name} (@{update.effective_user.username or 'no username'}) "
                    f"wants to move forward.\n\n"
                    f"Company: {state['discovered'].get('company', 'unknown')}\n"
                    f"Role: {state['discovered'].get('role', 'unknown')}\n"
                    f"Price: ${state.get('price_quoted', {}).get('monthly', '?')}/mo\n"
                )
                if email:
                    alert += f"Email: {email}\n\nForward me the onboarding contract and I'll send it to them."
                else:
                    alert += "\nWaiting for their email. I'll update you when they reply."
                await self.send_alert(self.admin_id, alert)

        await update.message.reply_text(response)

    # ── MESSAGING ─────────────────────────────────────────────────────

    async def send_alert(self, chat_id, message):
        if self.app:
            await self.app.bot.send_message(chat_id=chat_id, text=message)

    def send_sync(self, chat_id, message):
        import requests
        if not self.token:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            resp = requests.post(url, json={"chat_id": chat_id, "text": message})
            return resp.status_code == 200
        except Exception:
            return False

    # ── HELPERS ───────────────────────────────────────────────────────

    def _is_admin(self, update: Update):
        if not self.admin_id:
            return True
        return update.effective_user.id == self.admin_id

    def _extract_email(self, text):
        import re
        match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
        return match.group(0) if match else None

    # ── RUN ───────────────────────────────────────────────────────────

    def run(self):
        if not self.token:
            print("[Finch] TELEGRAM_BOT_TOKEN not set. Telegram disabled.")
            return

        self.app = Application.builder().token(self.token).build()

        # Admin commands
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("leads", self.cmd_leads))
        self.app.add_handler(CommandHandler("pipeline", self.cmd_pipeline))
        self.app.add_handler(CommandHandler("docs", self.cmd_docs))
        self.app.add_handler(CommandHandler("send", self.cmd_send))
        self.app.add_handler(CommandHandler("tag", self.cmd_tag))
        self.app.add_handler(CommandHandler("handoffs", self.cmd_handoffs))
        self.app.add_handler(CommandHandler("reset", self.cmd_reset))

        # Text messages
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

        # Document/file forwarding
        self.app.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))

        print("[Finch] Telegram listener active. Ready on your phone.")
        print("[Finch] Forward any file here — I'll store it and can email it to prospects.")
        self.app.run_polling()
