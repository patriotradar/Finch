"""Conservative outreach engine for Aegis."""

import os, smtplib, ssl, time, random
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from translator.plain_language import translate_text

# How prospects reach Finch directly — set these in config or env vars
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "FinchSecurityBot")
WEB_CHAT_URL = os.environ.get("FINCH_WEB_CHAT_URL", "https://finch.security/chat")


class OutreachEngine:
    def __init__(self, config=None, policy=None):
        self.config = config or {}
        self.smtp_host = self.config.get("smtp_host", "smtp.gmail.com")
        self.smtp_port = self.config.get("smtp_port", 587)
        self.daily_limit = min(int(self.config.get("daily_limit", 20)), 20)
        self.sent_today = 0
        self.followup_sequence = self.config.get("followup_sequence", [2, 5, 10])
        self.policy = policy

    def craft_initial_email(self, lead):
        company = lead.get("company", "your organization")
        vulns = lead.get("vulnerabilities", [])
        chat_ps = self._chat_postscript()
        if not vulns:
            return self._craft_generic_email(lead)
        best = vulns[0].get("finding", vulns[0].get("result", ""))
        best_plain = translate_text(best, "client")
        return {
            "subject": f"Public-information observation for {company}",
            "body": f"Hi,\n\nI'm Harold from Aegis. During a limited review of public "
                    f"information, Aegis recorded this potential indicator relating to {company}:\n\n"
                    f"{best_plain}\n\n"
                    f"This is not evidence of exploitation and should be verified by your IT team. "
                    f"I can explain the observation and the service limitations.\n\n"
                    f"{chat_ps}"
                    f"Best,\nHarold\nAegis"
        }

    def craft_followup_email(self, lead, seq):
        company = lead.get("company", "your company")
        templates = [
            {"subject": f"Re: Aegis information for {company}",
             "body": f"Hi,\n\nI wanted to follow up on my earlier note. I know inboxes are brutal — "
                     f"just making sure it didn't get buried. If passive public-information monitoring "
                     f"isn't relevant, no reply is needed and the opt-out link will stop all contact.\n\n"
                     f"Best,\nHarold\nAegis"},
            {"subject": f"Quick security tip for {company}",
             "body": f"Hi,\n\nOne free tip while you think about it: make sure SPF, DKIM, and DMARC are "
                     f"reviewed by your IT team. Aegis can record the public configuration but does "
                     f"not claim that a setting proves compromise.\n\nHarold\nAegis"},
            {"subject": f"Last note — {company}",
             "body": f"Hi,\n\nLast email from me. If the timing isn't right or you've got this covered, "
                     f"just say so and I'll stop.\n\nIf you do want the free assessment, reply \"yes.\"\n\n"
                     f"Either way, this is the final follow-up.\n\nHarold\nAegis"},
        ]
        return templates[min(seq - 1, len(templates) - 1)]

    def send_email(self, to_address, content, from_address=None, password=None, *, approved=False):
        if approved is not True:
            print("[Aegis] Outreach send blocked: policy approval required.")
            return False
        from_addr = from_address or os.environ.get("FINCH_EMAIL")
        passwd = password or os.environ.get("FINCH_EMAIL_PASSWORD")
        if not from_addr or not passwd:
            print("[Finch] Email credentials not set.")
            return False
        if self.sent_today >= self.daily_limit:
            return False
        msg = MIMEMultipart()
        msg["From"] = f"Harold from Aegis <{from_addr}>"
        msg["To"] = to_address
        msg["Subject"] = content["subject"]
        msg.attach(MIMEText(content["body"], "plain"))
        try:
            context = ssl.create_default_context()
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as s:
                s.starttls(context=context)
                s.login(from_addr, passwd)
                s.sendmail(from_addr, to_address, msg.as_string())
            self.sent_today += 1
            return True
        except Exception as e:
            print(f"[Finch] Email failed: {e}")
            return False

    def process_outreach_queue(self, leads, force=False):
        if self.policy is None:
            return ["Blocked: outreach policy is not configured"]
        actions = []
        now = datetime.now()
        delay = self.config.get("min_delay_between", 120)
        for lead in leads:
            history = lead.get("outreach", [])
            if not history:
                if self.sent_today >= self.daily_limit and not force:
                    break
                email = self.craft_initial_email(lead)
                allowed, reason = self.policy.can_contact(
                    lead.get("company", ""),
                    lead.get("contact_email", ""),
                    lead.get("contact_source", ""),
                    lead.get("business_type", ""),
                    consent=bool(lead.get("consent")),
                    followup=False,
                )
                if not allowed:
                    actions.append(f"Blocked ({reason}) → {lead.get('company', '?')}")
                    continue
                if self.send_email(lead.get("contact_email", ""), email, approved=True):
                    history.append({"type": "initial", "sent_at": now.isoformat(), "subject": email["subject"]})
                    lead["outreach"] = history
                    lead["stage"] = "outreached"
                    actions.append(f"Initial → {lead['company']}")
                time.sleep(delay + random.randint(0, 60))
                continue
            last = datetime.fromisoformat(history[-1]["sent_at"])
            days = (now - last).days
            seq = len(history)
            if seq <= len(self.followup_sequence) and days >= self.followup_sequence[seq - 1]:
                email = self.craft_followup_email(lead, seq)
                allowed, reason = self.policy.can_contact(
                    lead.get("company", ""),
                    lead.get("contact_email", ""),
                    lead.get("contact_source", ""),
                    lead.get("business_type", ""),
                    consent=bool(lead.get("consent")),
                    followup=True,
                )
                if not allowed:
                    actions.append(f"Blocked ({reason}) → {lead.get('company', '?')}")
                    continue
                if self.send_email(lead.get("contact_email", ""), email, approved=True):
                    history.append({"type": f"followup_{seq}", "sent_at": now.isoformat(), "subject": email["subject"]})
                    lead["stage"] = "followed_up"
                    actions.append(f"Follow-up {seq} → {lead['company']}")
                time.sleep(delay + random.randint(0, 60))
        return actions

    def _craft_generic_email(self, lead):
        company = lead.get("company", "your organization")
        chat_ps = self._chat_postscript()
        return {
            "subject": f"Passive public-information monitoring for {company}",
            "body": f"Hi,\n\nI'm Harold from Aegis. Aegis provides passive public-information "
                    f"monitoring for customer-approved internet assets.\n\n"
                    f"If that is relevant to {company}, you can read the short overview without "
                    f"booking a call.\n\n"
                    f"{chat_ps}"
                    f"Best,\nHarold\nAegis"
        }

    def _chat_postscript(self):
        """Build the 'talk to me directly' CTA for every email."""
        lines = [
            "P.S. — You can reply to this email, or talk to me directly:",
        ]
        if TELEGRAM_BOT_USERNAME and TELEGRAM_BOT_USERNAME != "FinchSecurityBot":
            lines.append(f"  Telegram: @{TELEGRAM_BOT_USERNAME}")
        if WEB_CHAT_URL:
            lines.append(f"  Web: {WEB_CHAT_URL}")
        lines.append("")
        return "\n".join(lines)
