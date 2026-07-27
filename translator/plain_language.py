"""
Plain-language translator — Finch explains complex security findings
to non-technical clients using analogies, risk framing, and business
impact language. Never condescending, always illuminating.
"""

import re

# Mapping of technical terms to plain-language explanations
TECH_TO_PLAIN = {
    "CVE": "a publicly known security weakness",
    "vulnerability": "a gap in your defenses that attackers could walk through",
    "exploit": "a working method attackers use to break in",
    "zero-day": "a flaw that even the software maker doesn't know about yet",
    "CVSS": "a score from 0 to 10 measuring how dangerous a weakness is",
    "RCE": "the ability for an attacker to run their own code on your server — full control",
    "SQL injection": "an attacker typing database commands into a login box to steal your data",
    "XSS": "an attacker slipping malicious code into your website that runs in your visitors' browsers",
    "CSRF": "tricking someone who's logged into your system into doing something they didn't intend",
    "SSRF": "tricking your server into fetching data from places it shouldn't — like your internal network",
    "IDOR": "changing a number in a URL to see someone else's private data",
    "path traversal": "an attacker navigating your server's file system through a web request",
    "auth bypass": "getting past the login door without a key",
    "privilege escalation": "starting as a regular user and working up to administrator — without permission",
    "lateral movement": "an attacker hopping from one compromised machine to the next inside your network",
    "C2": "a secret line of communication an attacker uses to control compromised machines",
    "exfiltration": "your data being quietly copied out the back door",
    "phishing": "a convincing fake email designed to trick someone into handing over credentials",
    "MFA": "a second lock on the door — even if someone steals your password, they still can't get in",
    "WAF": "a security filter that sits in front of your website, blocking obvious attacks",
    "IDS/IPS": "burglar alarms for your network — they watch for suspicious activity",
    "SIEM": "a central dashboard that collects and analyzes security events from across your organization",
    "SOC": "a team of people watching your security monitors 24/7 — or a service that does it for you",
    "pentest": "hiring someone to try to break in, so you know where to reinforce before real attackers arrive",
    "patch": "a fix from the software maker — like recalling a car part and replacing it before it fails",
    "firewall": "a gatekeeper that decides what traffic gets in and out of your network",
    "VPN": "an encrypted tunnel — like having a private road between two points that no one else can see",
    "DDoS": "flooding your website with so much traffic it collapses — like a mob blocking your store entrance",
    "ransomware": "software that locks up your files and demands payment to unlock them",
    "phishing": "an email that looks legitimate but is designed to steal credentials or install malware",
    "exposed port": "an unlocked window on your building visible from the street",
    "default credentials": "leaving the factory-set password on your equipment — same as leaving your key under the doormat",
    "unpatched": "running software with known holes that have already been fixed in newer versions",
    "misconfiguration": "a setting left in a dangerous state — usually by accident, but attackers look for exactly this",
    "SSL/TLS": "the encryption that keeps data private as it travels across the internet",
    "expired certificate": "your encryption has an expiration date, and it just passed — browsers will now warn visitors",
    "DNSSEC": "a way to verify that DNS responses haven't been tampered with",
    "SPF/DKIM/DMARC": "email authentication that proves emails from your domain are really from you — not an impostor",
}


def translate_term(term):
    """Translate a single technical term to plain language."""
    return TECH_TO_PLAIN.get(term.lower(), term)


def translate_text(text, audience="client"):
    """
    Translate technical text to plain language.
    
    audience: "client" (non-technical business owner), 
              "executive" (C-suite, cares about risk and money),
              "developer" (understands technical but benefits from framing)
    """
    result = text
    for tech_term, plain in TECH_TO_PLAIN.items():
        pattern = re.compile(r'\b' + re.escape(tech_term) + r'\b', re.IGNORECASE)
        result = pattern.sub(plain, result)

    if audience in ("client", "executive"):
        result = _add_business_context(result, audience)

    return result


def _add_business_context(text, audience):
    """Frame security findings in business terms."""
    business_framing = {
        "executive": "\n\nIn business terms: each of these issues represents a risk to revenue, "
                      "reputation, or regulatory compliance. I've ordered them by what I'd fix first "
                      "if I were managing your risk — highest impact, lowest effort at the top.",
        "client": "\n\nWhat this means for you: I've flagged the issues that need your attention. "
                  "Think of it like a home inspection — some items are urgent (the roof is leaking), "
                  "others are improvements (better locks on the windows). I'll walk you through each one."
    }
    return text + business_framing.get(audience, "")


def generate_analogy(technical_concept):
    """Generate an analogy for a technical concept using Finch's voice."""
    analogies = {
        "firewall": "A firewall is like the bouncer at a club. It checks IDs at the door "
                    "and only lets in people on the list. Without one, anyone walks in.",
        "encryption": "Encryption is like writing a letter in a code that only the recipient "
                      "can read. If someone intercepts the envelope, all they see is gibberish.",
        "patch management": "Patching is like fixing a recall on your car. The manufacturer "
                            "found a defect, issued a fix — but it only works if you bring the car in.",
        "MFA": "Multi-factor authentication is like needing both a key AND a fingerprint scan "
               "to open a door. Stealing the key isn't enough.",
        "backup": "Backups are insurance. You hope you never need them, but when ransomware hits, "
                  "they're the difference between a bad day and going out of business.",
        "IDS": "An intrusion detection system is a burglar alarm. It doesn't stop the break-in — "
               "but it tells you it's happening so you can respond.",
        "zero trust": "Zero trust means: never assume someone inside the building belongs there. "
                      "Verify everyone, every time, at every door.",
    }
    return analogies.get(technical_concept.lower())


def find_severity_score(text):
    """Extract and standardize severity mentions in text."""
    severities = {
        r'\bcritical\b': "🔴 CRITICAL — requires immediate action",
        r'\bhigh\b': "🟠 HIGH — address this week",
        r'\bmedium\b': "🟡 MEDIUM — address this month",
        r'\blow\b': "🟢 LOW — address when convenient",
        r'\binfo\b': "ℹ️ INFORMATIONAL — for awareness",
    }
    result = text
    for pattern, replacement in severities.items():
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result


def explain_finding(finding_dict, audience="client"):
    """
    Takes a vulnerability finding dict and returns a plain-language explanation
    suitable for the target audience.
    """
    name = finding_dict.get("name", "Unknown finding")
    severity = finding_dict.get("severity", "Unknown")
    description = finding_dict.get("description", "")
    affected = finding_dict.get("affected", "an asset")
    impact = finding_dict.get("impact", "")
    remediation = finding_dict.get("remediation", "")

    explanation = f"""I found something on {affected}.

The issue: {name}
Severity: {severity}

What happened: {translate_text(description, audience)}

Why it matters: {translate_text(impact, audience)}

What to do: {translate_text(remediation, audience)}
"""
    return explanation
