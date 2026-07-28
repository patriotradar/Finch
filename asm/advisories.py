"""Conservative published-advisory matching for disclosed technologies."""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import AssetSnapshot, MonitoringRequest, Observation, ObservationStatus
from core.tenant import require_monitorable_asset


CISA_KEV_URL = (
    "https://www.cisa.gov/sites/default/files/feeds/"
    "known_exploited_vulnerabilities.json"
)


def _default_fetcher(url: str) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "AegisPassiveMonitor/1.0", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        return int(response.status), json.loads(response.read(5_000_000).decode("utf-8"))


def _technology_words(payload: dict) -> set[str]:
    headers = payload.get("headers") or {}
    disclosed = " ".join([
        str(headers.get("server") or ""),
        str(headers.get("x-powered-by") or ""),
    ]).lower()
    return {
        word for word in re.findall(r"[a-z][a-z0-9._+-]{2,}", disclosed)
        if word not in {"server", "powered", "http"}
    }


class AdvisoryService:
    def __init__(self, session: Session, fetcher: Callable = _default_fetcher):
        self.session = session
        self.fetcher = fetcher

    def match_public_advisories(self, workspace_id: str, asset_id: str) -> list[Observation]:
        asset = require_monitorable_asset(self.session, workspace_id, asset_id)
        snapshot = self.session.scalar(
            select(AssetSnapshot).where(
                AssetSnapshot.asset_id == asset.id,
                AssetSnapshot.source_type == "HTTP headers",
            )
        )
        technologies = _technology_words(snapshot.payload if snapshot else {})
        if not technologies:
            return []
        try:
            status, feed = self.fetcher(CISA_KEV_URL)
            outcome = "completed" if status == 200 else "stopped"
            reason = None if status == 200 else f"http_{status}"
        except Exception as error:
            status, feed, outcome, reason = None, {}, "stopped", type(error).__name__
        self.session.add(MonitoringRequest(
            workspace_id=workspace_id,
            asset_id=asset.id,
            source_type="Published advisories",
            method="GET",
            target=CISA_KEV_URL,
            response_status=status,
            outcome=outcome,
            stopped_reason=reason,
        ))
        if outcome != "completed":
            return []
        matches = []
        for advisory in (feed.get("vulnerabilities") or [])[:5000]:
            product_text = " ".join([
                str(advisory.get("vendorProject") or ""),
                str(advisory.get("product") or ""),
            ]).lower()
            if not any(
                re.search(rf"(?<![a-z0-9]){re.escape(tech)}(?![a-z0-9])", product_text)
                for tech in technologies
            ):
                continue
            cve = str(advisory.get("cveID") or "").strip()
            observation = Observation(
                workspace_id=workspace_id,
                asset_id=asset.id,
                status=ObservationStatus.POTENTIAL_INDICATOR,
                title=f"Published advisory may relate to disclosed technology: {cve}",
                observation=(
                    f"The public HTTP response disclosed a technology name that also appears "
                    f"in the CISA KEV catalogue entry {cve}."
                ),
                inference=(
                    "The asset may warrant version verification. A name match does not establish "
                    "that the affected product or version is installed."
                ),
                severity="unknown",
                confidence="low",
                source=f"https://www.cisa.gov/known-exploited-vulnerabilities-catalog?search_api_fulltext={cve}",
                false_positive_guidance=(
                    "Confirm the actual product, version and deployment with the customer's IT team."
                ),
                missing_evidence="Installed product and version were not authenticated or verified.",
                it_verification_required=True,
            )
            self.session.add(observation)
            matches.append(observation)
            if len(matches) >= 20:
                break
        return matches
