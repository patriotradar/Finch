"""
Aegis lead pipeline — Vercel-safe end-to-end discovery.

Domain → light external findings → public contact emails → draft/send-ready lead.
No nuclei, no Shodan subprocess. Pure HTTPS + DNS so it runs on serverless.
"""
from __future__ import annotations

import json
import os
import re
import socket
import ssl
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parseaddr
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

USER_AGENT = "AegisResearch/1.0 (+https://aegis.security; authorized marketing recon)"
EMAIL_RE = re.compile(
    r"(?i)\b([a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,})\b"
)
ROLE_LOCALS = (
    "security", "ciso", "infosec", "soc", "abuse",
    "it", "admin", "ops", "opssec", "contact",
    "hello", "info", "support", "privacy", "legal",
    "ceo", "cto", "founders", "team",
)
SKIP_LOCALS = {
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "mailer-daemon", "postmaster", "bounce", "notifications",
    "newsletter", "news", "marketing", "unsubscribe",
}
PAGE_PATHS = (
    "/", "/contact", "/contact-us", "/about", "/about-us",
    "/team", "/company", "/security", "/privacy", "/legal",
    "/impressum", "/support",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _http_get(url: str, timeout: float = 6.0) -> Tuple[int, str, str]:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json,*/*"})
    try:
        with urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            raw = resp.read(250_000)
            charset = "utf-8"
            ctype = resp.headers.get_content_charset()
            if ctype:
                charset = ctype
            try:
                text = raw.decode(charset, errors="replace")
            except Exception:
                text = raw.decode("utf-8", errors="replace")
            return int(getattr(resp, "status", 200) or 200), text, str(resp.geturl())
    except HTTPError as e:
        try:
            body = e.read(80_000).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return int(e.code), body, url
    except Exception:
        return 0, "", url


def normalize_domain(raw: str) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return ""
    if "://" not in s:
        s = "https://" + s
    host = urlparse(s).hostname or raw.strip().lower()
    host = host.lstrip(".").removeprefix("www.")
    # company name without TLD — reject bare words without a dot unless user passed FQDN-ish
    return host.strip().lower()


def company_from_domain(domain: str) -> str:
    base = domain.split(".")[0] if domain else "Company"
    return base.replace("-", " ").replace("_", " ").title()


class LeadPipeline:
    def __init__(self, data_dir: str):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "leads.json"
        self._data: Dict[str, Any] = {"leads": []}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
                self._data.setdefault("leads", [])
            except Exception:
                self._data = {"leads": []}

    def _save(self) -> None:
        try:
            self.path.write_text(json.dumps(self._data, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            print(f"[Pipeline] save failed: {e}")

    def list_leads(self, limit: int = 50) -> List[Dict[str, Any]]:
        leads = sorted(self._data["leads"], key=lambda x: x.get("updated_at") or "", reverse=True)
        return leads[:limit]

    def get(self, lead_id: str) -> Optional[Dict[str, Any]]:
        for lead in self._data["leads"]:
            if lead.get("id") == lead_id:
                return lead
        return None

    def delete(self, lead_id: str) -> bool:
        before = len(self._data["leads"])
        self._data["leads"] = [l for l in self._data["leads"] if l.get("id") != lead_id]
        if len(self._data["leads"]) != before:
            self._save()
            return True
        return False

    def summary(self) -> Dict[str, int]:
        leads = self._data["leads"]
        return {
            "total": len(leads),
            "researched": sum(1 for l in leads if l.get("stage") in ("researched", "ready", "drafted", "sent")),
            "with_emails": sum(1 for l in leads if l.get("emails")),
            "ready": sum(1 for l in leads if l.get("stage") == "ready"),
            "sent": sum(1 for l in leads if l.get("stage") == "sent"),
        }

    def has_mx(self, domain: str) -> bool:
        try:
            import dns.resolver  # type: ignore
            answers = dns.resolver.resolve(domain, "MX")
            return bool(list(answers))
        except Exception:
            pass
        # fallback: any A/AAAA
        try:
            socket.getaddrinfo(domain, 443)
            return True
        except Exception:
            try:
                socket.getaddrinfo(domain, 80)
                return True
            except Exception:
                return False

    def crtsh_hosts(self, domain: str, limit: int = 12) -> List[str]:
        url = f"https://crt.sh/?q=%25.{domain}&output=json"
        code, body, _ = _http_get(url, timeout=8.0)
        if code != 200 or not body.strip():
            return []
        hosts: List[str] = []
        seen = set()
        try:
            # crt.sh sometimes returns concatenated JSON objects; try standard load first
            data = json.loads(body)
        except json.JSONDecodeError:
            # try line-ish repair: find array
            start = body.find("[")
            end = body.rfind("]")
            if start >= 0 and end > start:
                try:
                    data = json.loads(body[start : end + 1])
                except Exception:
                    return []
            else:
                return []
        if not isinstance(data, list):
            return []
        for row in data:
            name = str(row.get("name_value") or "")
            for part in name.split("\n"):
                h = part.strip().lower().lstrip("*.")
                if not h or h in seen:
                    continue
                if h == domain or h.endswith("." + domain):
                    seen.add(h)
                    hosts.append(h)
                if len(hosts) >= limit:
                    return hosts
        return hosts

    def light_findings(self, domain: str, extra_hosts: Optional[List[str]] = None) -> List[Dict[str, str]]:
        findings: List[Dict[str, str]] = []
        hosts = [domain] + [h for h in (extra_hosts or []) if h != domain][:6]

        # MX / reachability
        if self.has_mx(domain):
            findings.append({
                "id": "mx-ok",
                "title": "Mail domain resolves",
                "detail": f"{domain} accepts mail (MX/A present). Good cold-email target.",
                "severity": "info",
            })
        else:
            findings.append({
                "id": "mx-missing",
                "title": "No clear MX",
                "detail": f"Could not confirm MX for {domain}. Verify the domain before sending.",
                "severity": "low",
            })

        # Homepage security headers + tech signals
        for scheme in ("https", "http"):
            code, body, final = _http_get(f"{scheme}://{domain}/", timeout=6.0)
            if code == 0:
                continue
            findings.append({
                "id": "web-live",
                "title": f"Web property live ({code})",
                "detail": f"{final or domain} answered HTTP {code}.",
                "severity": "info",
            })
            # Redirect to third party?
            try:
                fin_host = urlparse(final).hostname or ""
                if fin_host and domain not in fin_host and not fin_host.endswith("." + domain):
                    findings.append({
                        "id": "off-domain-redirect",
                        "title": "Homepage redirects off primary domain",
                        "detail": f"{domain} → {fin_host}",
                        "severity": "low",
                    })
            except Exception:
                pass
            lower = body.lower()
            missing = []
            # crude header check via second request with context unavailable — sniff meta
            if "content-security-policy" not in lower and "content-security-policy" not in str(body[:500]).lower():
                missing.append("CSP (not advertised in HTML)")
            if "swagger" in lower or "api-docs" in lower or "/openapi" in lower:
                findings.append({
                    "id": "api-docs",
                    "title": "Possible public API docs",
                    "detail": "Swagger/OpenAPI markers found on the homepage response — often worth a closer look.",
                    "severity": "medium",
                })
            if "staging" in lower or "dev environment" in lower:
                findings.append({
                    "id": "staging-bleed",
                    "title": "Staging/dev language on production page",
                    "detail": "Production HTML mentions staging/dev — sometimes indicates messy asset hygene.",
                    "severity": "medium",
                })
            break  # only need one successful scheme

        # Interesting related hosts from CT
        interesting = []
        for h in hosts:
            leaf = h.split(".")[0]
            if any(k in leaf for k in ("stage", "staging", "dev", "test", "vpn", "admin", "intranet", "old", "backup", "jenkins", "grafana", "jira")):
                interesting.append(h)
        for h in interesting[:5]:
            code, _, final = _http_get(f"https://{h}/", timeout=4.0)
            if code and code < 500:
                findings.append({
                    "id": f"host-{h}",
                    "title": f"Related host responds: {h}",
                    "detail": f"Certificate history based host {h} returned HTTP {code}. Common attacker recon target.",
                    "severity": "medium" if any(x in h for x in ("stage", "dev", "admin", "vpn")) else "low",
                })

        if any(f.get("id", "").startswith("host-") for f in findings):
            pass
        elif host_count := len(hosts):
            if host_count > 3:
                findings.append({
                    "id": "ct-surface",
                    "title": f"{host_count} hostnames in certificate history",
                    "detail": f"crt.sh shows multiple names under {domain}. Larger external surface → higher monitoring value.",
                    "severity": "info",
                })

        # De-dupe by id
        out = []
        seen = set()
        for f in findings:
            if f["id"] in seen:
                continue
            seen.add(f["id"])
            out.append(f)
        return out

    def _email_ok(self, email: str, domain: str) -> bool:
        email = email.strip().lower()
        if not EMAIL_RE.fullmatch(email):
            # EMAIL_RE has group - use search
            if not EMAIL_RE.match(email):
                return False
        try:
            local, host = email.rsplit("@", 1)
        except ValueError:
            return False
        if local.split("+")[0] in SKIP_LOCALS:
            return False
        if any(x in local for x in ("noreply", "no-reply", "mailer")):
            return False
        # Prefer same registered domain
        if host != domain and not host.endswith("." + domain):
            # allow parent corporate domains if input was subdomain
            if domain not in host and host not in domain:
                return False
        if len(email) > 80:
            return False
        return True

    def scrape_public_emails(self, domain: str) -> List[Dict[str, Any]]:
        found: Dict[str, Dict[str, Any]] = {}
        urls = [f"https://{domain}{path}" for path in PAGE_PATHS]
        urls += [f"http://{domain}{path}" for path in ("/", "/contact", "/about")]

        def fetch(url: str):
            return url, _http_get(url, timeout=5.0)

        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = [pool.submit(fetch, u) for u in urls[:10]]
            for fut in as_completed(futs):
                try:
                    url, (code, body, final) = fut.result()
                except Exception:
                    continue
                if code == 0 or not body:
                    continue
                text = unescape(body)
                # mailto:
                for m in re.findall(r"mailto:([^\"'\s\?&>]+)", text, flags=re.I):
                    addr = parseaddr(f"x<{m}>")[1] or m
                    addr = addr.strip().lower()
                    if self._email_ok(addr, domain):
                        found[addr] = {
                            "email": addr,
                            "source": "mailto",
                            "confidence": 0.9,
                            "page": final or url,
                            "kind": "public",
                        }
                for m in EMAIL_RE.findall(text):
                    addr = m.strip().lower()
                    if self._email_ok(addr, domain):
                        prev = found.get(addr)
                        conf = 0.75
                        if prev:
                            conf = max(prev.get("confidence", 0), conf)
                        found[addr] = {
                            "email": addr,
                            "source": "website",
                            "confidence": conf,
                            "page": final or url,
                            "kind": "public",
                        }
        return sorted(found.values(), key=lambda x: -x.get("confidence", 0))

    def role_emails(self, domain: str) -> List[Dict[str, Any]]:
        if not self.has_mx(domain) and not self.has_mx(domain):
            return []
        out = []
        for local in ROLE_LOCALS:
            addr = f"{local}@{domain}"
            # higher conf for security-relevant roles
            conf = 0.55 if local in ("security", "ciso", "infosec", "abuse", "soc") else 0.4
            if local in ("contact", "hello", "info"):
                conf = 0.45
            out.append({
                "email": addr,
                "source": "role_guess",
                "confidence": conf,
                "kind": "role",
                "note": "Common role mailbox — verify deliverability before heavy sequencing.",
            })
        return out

    def hunter_emails(self, domain: str) -> List[Dict[str, Any]]:
        key = os.environ.get("HUNTER_API_KEY") or os.environ.get("HUNTER_KEY")
        if not key:
            return []
        url = f"https://api.hunter.io/v2/domain-search?domain={domain}&api_key={key}&limit=10"
        code, body, _ = _http_get(url, timeout=8.0)
        if code != 200 or not body:
            return []
        try:
            data = json.loads(body)
        except Exception:
            return []
        emails = []
        for row in (data.get("data") or {}).get("emails") or []:
            addr = (row.get("value") or "").strip().lower()
            if not addr or not self._email_ok(addr, domain):
                continue
            conf = float(row.get("confidence") or 50) / 100.0
            emails.append({
                "email": addr,
                "source": "hunter",
                "confidence": conf,
                "kind": "person",
                "name": " ".join(x for x in [row.get("first_name"), row.get("last_name")] if x).strip() or None,
                "position": row.get("position"),
            })
        return emails

    def refine_email_list(self, scraped: List[Dict], hunter: List[Dict], roles: List[Dict]) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        for bucket in (hunter, scraped, roles):
            for item in bucket:
                addr = item["email"].lower()
                prev = merged.get(addr)
                if not prev or item.get("confidence", 0) > prev.get("confidence", 0):
                    merged[addr] = item
                elif prev and item.get("source") != prev.get("source"):
                    prev["source"] = f"{prev.get('source')}+{item.get('source')}"
        # Rank: hunter/person > public website > security roles > generic roles
        def score(e: Dict[str, Any]) -> float:
            s = float(e.get("confidence") or 0)
            if e.get("source") == "hunter" or e.get("kind") == "person":
                s += 0.3
            if e.get("source") in ("website", "mailto"):
                s += 0.2
            local = e["email"].split("@")[0]
            if local in ("security", "ciso", "infosec", "abuse"):
                s += 0.15
            if e.get("kind") == "role" and local in ("info", "hello", "support"):
                s -= 0.05
            return s

        ranked = sorted(merged.values(), key=score, reverse=True)
        # Cap roles if we already have strong public/hunter hits
        strong = [e for e in ranked if e.get("kind") in ("public", "person") or e.get("source") in ("hunter", "website", "mailto")]
        if strong:
            extras = [e for e in ranked if e not in strong and e.get("kind") == "role"]
            security_roles = [e for e in extras if e["email"].split("@")[0] in ("security", "ciso", "infosec", "abuse", "soc")]
            return (strong + security_roles)[:12]
        return ranked[:12]

    def research(self, domain_or_url: str, company: str = "") -> Dict[str, Any]:
        domain = normalize_domain(domain_or_url)
        if not domain or "." not in domain:
            return {"ok": False, "error": "Enter a real domain like acme.com"}

        hosts = self.crtsh_hosts(domain)
        findings = self.light_findings(domain, hosts)

        scraped = self.scrape_public_emails(domain)
        hunter = self.hunter_emails(domain)
        roles = self.role_emails(domain)
        emails = self.refine_email_list(scraped, hunter, roles)

        # Prefer a security-flavored finding for the outreach hook
        hook = ""
        for pref in ("medium", "high", "low", "info"):
            for f in findings:
                if f.get("severity") == pref and f.get("id") not in ("mx-ok", "web-live"):
                    hook = f.get("detail") or f.get("title") or ""
                    break
            if hook:
                break
        if not hook and findings:
            hook = findings[0].get("detail") or findings[0].get("title") or ""

        lead = {
            "id": "lead-" + uuid.uuid4().hex[:10],
            "domain": domain,
            "company": (company or company_from_domain(domain)).strip(),
            "stage": "ready" if emails else "researched",
            "findings": findings,
            "hosts": hosts[:20],
            "emails": emails,
            "selected_email": (emails[0]["email"] if emails else ""),
            "hook": hook,
            "created_at": _now(),
            "updated_at": _now(),
            "hunter_configured": bool(os.environ.get("HUNTER_API_KEY") or os.environ.get("HUNTER_KEY")),
        }

        # upsert by domain
        self._data["leads"] = [l for l in self._data["leads"] if l.get("domain") != domain]
        self._data["leads"].insert(0, lead)
        self._data["leads"] = self._data["leads"][:80]
        self._save()
        return {"ok": True, "lead": lead}

    def select_email(self, lead_id: str, email: str) -> Optional[Dict[str, Any]]:
        lead = self.get(lead_id)
        if not lead:
            return None
        lead["selected_email"] = email.strip().lower()
        lead["updated_at"] = _now()
        if lead.get("emails") and lead["selected_email"]:
            lead["stage"] = "ready"
        self._save()
        return lead

    def mark_drafted(self, lead_id: str, message_id: str = "") -> Optional[Dict[str, Any]]:
        lead = self.get(lead_id)
        if not lead:
            return None
        lead["stage"] = "drafted"
        lead["draft_message_id"] = message_id
        lead["updated_at"] = _now()
        self._save()
        return lead

    def mark_sent(self, lead_id: str, message_id: str = "") -> Optional[Dict[str, Any]]:
        lead = self.get(lead_id)
        if not lead:
            return None
        lead["stage"] = "sent"
        lead["sent_message_id"] = message_id
        lead["updated_at"] = _now()
        self._save()
        return lead

    def best_hook(self, lead: Dict[str, Any]) -> str:
        return (lead.get("hook") or "").strip()
