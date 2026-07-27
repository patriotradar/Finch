"""
Report generator — Finch produces client-ready reports in plain language.
"""

import json
from datetime import datetime
from translator.plain_language import translate_text, explain_finding, find_severity_score


class ReportGenerator:
    def __init__(self, config=None):
        self.config = config or {}

    def generate_executive_summary(self, client_name, scan_data, audience="executive"):
        """One-page executive summary of findings."""
        findings = scan_data.get("findings", [])
        assets = scan_data.get("assets", {})

        critical = sum(1 for f in findings if f.get("info", {}).get("severity", "").lower() == "critical")
        high = sum(1 for f in findings if f.get("info", {}).get("severity", "").lower() == "high")
        medium = sum(1 for f in findings if f.get("info", {}).get("severity", "").lower() == "medium")
        live = len(assets.get("live_hosts", []))

        summary = f"""Security Assessment — {client_name}
======================================
Date: {datetime.now().strftime('%B %d, %Y')}

What we found:
  Assets we can see from the outside: {live}
  Critical issues (fix immediately): {critical}
  High-risk issues (fix this week): {high}
  Medium-risk issues (fix this month): {medium}

"""
        if critical > 0:
            summary += "URGENT: You have critical vulnerabilities that are actively exploitable. "
            summary += "Attackers scan for these constantly. We recommend addressing them within 48 hours.\n\n"

        if high > 0:
            summary += "IMPORTANT: Your high-risk issues should be addressed within the week. "
            summary += "These are not emergencies, but they significantly weaken your security posture.\n\n"

        if critical == 0 and high == 0:
            summary += "Good news: No critical or high-risk issues found. Your external posture is relatively "
            summary += "well-maintained. The medium and low findings below represent opportunities for improvement.\n"

        return summary

    def generate_full_report(self, client_name, scan_data, audience="client"):
        """Full detailed report with plain-language explanations."""
        summary = self.generate_executive_summary(client_name, scan_data, audience)
        findings = scan_data.get("findings", [])

        sections = [summary, "\n" + "=" * 60 + "\n", "DETAILED FINDINGS\n", "=" * 60 + "\n"]

        for i, finding in enumerate(sorted(findings, key=lambda f: {
            "critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4
        }.get(f.get("info", {}).get("severity", "").lower(), 5))):

            name = finding.get("info", {}).get("name", "Unknown")
            severity = finding.get("info", {}).get("severity", "Unknown").upper()
            desc = finding.get("info", {}).get("description", "")
            remediation = finding.get("info", {}).get("remediation", "Contact vendor for patch.")

            sections.append(f"\n--- Finding #{i+1}: {name} ---")
            sections.append(f"Severity: {find_severity_score(severity)}")
            sections.append(f"\nWhat it is: {translate_text(desc, audience)}")
            sections.append(f"\nWhat to do: {translate_text(remediation, audience)}")
            sections.append("\n")

        return "\n".join(sections)

    def save_report(self, client_name, scan_data, output_path=None, audience="client"):
        """Save report to file."""
        if output_path is None:
            output_path = f"./data/clients/{client_name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.txt"

        report = self.generate_full_report(client_name, scan_data, audience)
        with open(output_path, "w") as f:
            f.write(report)
        return output_path
