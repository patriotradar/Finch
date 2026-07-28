"""Passive, public-information-only monitoring for customer-approved domains."""

from __future__ import annotations

import hashlib
import http.client
import json
import socket
import ssl
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

import dns.resolver
from sqlalchemy.orm import Session

from core.models import Evidence, MonitoringRequest, Observation, ObservationStatus
from core.tenant import require_monitorable_asset


MAX_REQUESTS_PER_RUN = 8
TIMEOUT_SECONDS = 6
SAFE_HTTP_METHODS = {"HEAD", "GET"}
STOP_STATUSES = {429, 500, 502, 503, 504}


@dataclass
class PublicResult:
    source_type: str
    method: str
    target: str
    status: int | None
    payload: dict[str, Any]
    outcome: str = "completed"
    stopped_reason: str | None = None


class PassiveMonitor:
    """Collect DNS, TLS and root HTTP metadata without offensive interaction."""

    def __init__(self, session: Session, collectors: list[Callable[[str], PublicResult]] | None = None):
        self.session = session
        self.collectors = collectors or [self.collect_dns, self.collect_tls, self.collect_http_headers]

    def monitor_asset(self, workspace_id: str, asset_id: str) -> list[Observation]:
        asset = require_monitorable_asset(self.session, workspace_id, asset_id)
        observations: list[Observation] = []
        for collector in self.collectors[:MAX_REQUESTS_PER_RUN]:
            result = collector(asset.value)
            self._validate_result(result, asset.value)
            self.session.add(MonitoringRequest(
                workspace_id=workspace_id,
                asset_id=asset.id,
                source_type=result.source_type,
                method=result.method,
                target=result.target,
                response_status=result.status,
                outcome=result.outcome,
                stopped_reason=result.stopped_reason,
            ))
            if result.outcome != "completed":
                break
            observation = Observation(
                workspace_id=workspace_id,
                asset_id=asset.id,
                status=ObservationStatus.OBSERVATION,
                title=f"Public {result.source_type} metadata",
                observation=json.dumps(result.payload, sort_keys=True),
                inference=None,
                severity="informational",
                confidence="high",
                source=result.target,
                false_positive_guidance="Verify the current configuration with the asset owner.",
                missing_evidence="No authenticated or protected-system evidence was collected.",
                it_verification_required=True,
            )
            self.session.add(observation)
            self.session.flush()
            encoded = json.dumps(result.payload, sort_keys=True).encode("utf-8")
            self.session.add(Evidence(
                workspace_id=workspace_id,
                observation_id=observation.id,
                source_url=result.target,
                content_hash=hashlib.sha256(encoded).hexdigest(),
                metadata_json=result.payload,
            ))
            observations.append(observation)
        return observations

    @staticmethod
    def _validate_result(result: PublicResult, approved_domain: str) -> None:
        if result.method not in SAFE_HTTP_METHODS:
            raise ValueError("unsafe_method_blocked")
        if (urlparse(result.target).hostname or "").lower() != approved_domain.lower():
            raise ValueError("target_outside_approved_asset")

    @staticmethod
    def collect_dns(domain: str) -> PublicResult:
        payload: dict[str, Any] = {}
        try:
            for record_type in ("A", "AAAA", "MX", "TXT"):
                try:
                    answers = dns.resolver.resolve(domain, record_type, lifetime=TIMEOUT_SECONDS)
                    payload[record_type] = sorted(str(answer)[:500] for answer in answers)[:20]
                except Exception:
                    payload[record_type] = []
            return PublicResult("DNS", "GET", f"dns://{domain}", None, payload)
        except Exception as error:
            return PublicResult(
                "DNS", "GET", f"dns://{domain}", None, {},
                outcome="stopped", stopped_reason=type(error).__name__,
            )

    @staticmethod
    def collect_tls(domain: str) -> PublicResult:
        try:
            context = ssl.create_default_context()
            with socket.create_connection((domain, 443), timeout=TIMEOUT_SECONDS) as raw:
                with context.wrap_socket(raw, server_hostname=domain) as secured:
                    certificate = secured.getpeercert()
                    payload = {
                        "version": secured.version(),
                        "subject": certificate.get("subject", []),
                        "issuer": certificate.get("issuer", []),
                        "notBefore": certificate.get("notBefore"),
                        "notAfter": certificate.get("notAfter"),
                        "subjectAltName": certificate.get("subjectAltName", [])[:50],
                    }
            return PublicResult("TLS", "GET", f"tls://{domain}:443", None, payload)
        except Exception as error:
            return PublicResult(
                "TLS", "GET", f"tls://{domain}:443", None, {},
                outcome="stopped", stopped_reason=type(error).__name__,
            )

    @staticmethod
    def collect_http_headers(domain: str) -> PublicResult:
        connection = http.client.HTTPSConnection(domain, 443, timeout=TIMEOUT_SECONDS)
        try:
            connection.request(
                "HEAD",
                "/",
                headers={"User-Agent": "AegisPassiveMonitor/1.0", "Accept": "*/*"},
            )
            response = connection.getresponse()
            status = int(response.status)
            payload = {"headers": {key.lower(): value[:1000] for key, value in response.getheaders()}}
            if status in STOP_STATUSES:
                return PublicResult(
                    "HTTP headers", "HEAD", f"https://{domain}/", status, payload,
                    outcome="stopped", stopped_reason=f"http_{status}",
                )
            return PublicResult("HTTP headers", "HEAD", f"https://{domain}/", status, payload)
        except Exception as error:
            return PublicResult(
                "HTTP headers", "HEAD", f"https://{domain}/", None, {},
                outcome="stopped", stopped_reason=type(error).__name__,
            )
        finally:
            connection.close()
