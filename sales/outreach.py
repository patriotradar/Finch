"""Outreach engine — Finch sends personalized cold emails."""

import os, smtplib, ssl, time, random
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from translator.plain_language import translate_text

# How prospects reach Finch directly — set these in config or env vars
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "FinchSecurityBot")
WEB_CHAT_URL = os.environ.get("FINCH_WEB_CHAT_URL", "https://finch.security/chat")


class OutreachEngine:
    def __init__(self, config=None):
        self.config = config or {}
        self.smtp_host = self.config.get("smtp_host", "smtp.gmail.com")
        self.smtp_port = self.config.get("smtp_port", 587)
        self.daily_limit = self.config.get("daily_limit", 30)
        self.sent_today = 0
        self.followup_sequence = self.config.get("followup_sequence", [2, 5, 10])

    def craft_initial_email(self, lead):
        company = lead.get("company", "your organization")
        vulns = lead.get("vulnerabilities", [])
        chat_ps = self._chat_postscript()
        if not vulns:
            return self._craft_generic_email(lead)
        best = vulns[0].get("finding", vulns[0].get("result", ""))
        best_plain = translate_text(best, "client")
        return {
            "subject": f"Security finding for {company}",
            "body": f"Hi,\n\nI'm Harold Finch, technical co-founder at Finch Security. We monitor "
                    f"companies' external infrastructure — and {company} came up in our scans.\n\n"
                    f"I found something on your domain I'd want to know about if it were mine:\n\n"
                    f"{best_plain}\n\n"
                    f"I'd be happy to walk you through the full picture — no pitch, just what "
                    f"I found and what it means in plain English.\n\n"
                    f"{chat_ps}"
                    f"Best,\nHarold Finch\n"
                    f"Technical Co-Founder, Finch Security"
        }

    def craft_followup_email(self, lead, seq):
        company = lead.get("company", "your company")
        templates = [
            {"subject": f"Re: Security finding for {company}",
             "body": f"Hi,\n\nI wanted to follow up on my earlier note. I know inboxes are brutal — "
                     f"just making sure it didn't get buried.\n\nThe short version: your external attack "
                     f"surface has exposures that attackers actively scan for. I can show you what and how "
                     f"to fix it.\n\nNo pressure.\n\nHarold"},
            {"subject": f"Quick security tip for {company}",
             "body": f"Hi,\n\nOne free tip while you think about it: make sure SPF, DKIM, and DMARC are "
                     f"set up. This alone stops 90% of domain impersonation attacks. Happy to verify yours.\n\n"
                     f"Harold"},
            {"subject": f"Last note — {company}",
             "body": f"Hi,\n\nLast email from me. If the timing isn't right or you've got this covered, "
                     f"just say so and I'll stop.\n\nIf you do want the free assessment, reply \"yes.\"\n\n"
                     f"Either way — I hope you never need what I build.\n\nHarold"},
        ]
        return templates[min(seq - 1, len(templates) - 1)]

    def send_email(self, to_address, content, from_address=None, password=None):
        from_addr = from_address or os.environ.get("FINCH_EMAIL")
        passwd = password or os.environ.get("FINCH_EMAIL_PASSWORD")
        if not from_addr or not passwd:
            print("[Finch] Email credentials not set.")
            return False
        if self.sent_today >= self.daily_limit:
            return False
        msg = MIMEMultipart()
        msg["From"] = f"Harold Finch <{from_addr}>"
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
        actions = []
        now = datetime.now()
        delay = self.config.get("min_delay_between", 120)
        for lead in leads:
            history = lead.get("outreach", [])
            if not history:
                if self.sent_today >= self.daily_limit and not force:
                    break
                email = self.craft_initial_email(lead)
                if self.send_email(lead.get("contact_email", ""), email):
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
                if self.send_email(lead.get("contact_email", ""), email):
                    history.append({"type": f"followup_{seq}", "sent_at": now.isoformat(), "subject": email["subject"]})
                    lead["stage"] = "followed_up"
                    actions.append(f"Follow-up {seq} → {lead['company']}")
                time.sleep(delay + random.randint(0, 60))
        return actions

    def _craft_generic_email(self, lead):
        company = lead.get("company", "your organization")
        chat_ps = self._chat_postscript()
        return {
            "subject": f"Your external attack surface — {company}",
            "body": f"Hi,\n\nI'm Harold Finch, technical co-founder at Finch Security. We monitor "
                    f"companies' external infrastructure — and {company} has a digital footprint "
                    f"worth protecting.\n\n"
                    f"I'd be happy to show you what's visible from the outside — no charge, no "
                    f"commitment. Takes about 90 seconds.\n\n"
                    f"{chat_ps}"
                    f"Best,\nHarold Finch\n"
                    f"Technical Co-Founder, Finch Security"
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
