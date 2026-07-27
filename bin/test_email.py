#!/usr/bin/env python3
"""Test your email setup in 1 second."""
import os, smtplib, ssl

from_addr = os.environ.get("FINCH_EMAIL")
password = os.environ.get("FINCH_EMAIL_PASSWORD")

if not from_addr or not password:
    print("❌  FINCH_EMAIL and/or FINCH_EMAIL_PASSWORD not set.")
    print("   Run: export FINCH_EMAIL=\"you@gmail.com\"")
    print("        export FINCH_EMAIL_PASSWORD=\"your-16-char-app-password\"")
    exit(1)

print(f"📧 Testing SMTP login for {from_addr}...")
try:
    ctx = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as s:
        s.starttls(context=ctx)
        s.login(from_addr, password)
    print(f"✅  Login successful — {from_addr} is ready.")
    print(f"   Finch can now send outreach emails and respond to replies.")
except smtplib.SMTPAuthenticationError:
    print(f"❌  Authentication failed. Check:")
    print(f"   - Is {from_addr} a Gmail address?")
    print(f"   - Did you use an App Password (not your normal password)?")
    print(f"   - Go to myaccount.google.com/apppasswords to create one.")
except Exception as e:
    print(f"❌  Connection failed: {e}")
