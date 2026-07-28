"""Evidence-led web report generation for Aegis workspaces."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import ApprovedAsset, Evidence, Observation, Report


class ReportService:
    def __init__(self, session: Session):
        self.session = session

    def generate_monthly(
        self, workspace_id: str, period_start: datetime, period_end: datetime
    ) -> Report:
        observations = self.session.scalars(
            select(Observation).where(
                Observation.workspace_id == workspace_id,
                Observation.detected_at >= period_start,
                Observation.detected_at < period_end,
            ).order_by(Observation.detected_at)
        ).all()
        assets = {
            item.id: item.value
            for item in self.session.scalars(
                select(ApprovedAsset).where(ApprovedAsset.workspace_id == workspace_id)
            )
        }
        items = []
        for observation in observations:
            evidence = self.session.scalars(
                select(Evidence).where(
                    Evidence.workspace_id == workspace_id,
                    Evidence.observation_id == observation.id,
                )
            ).all()
            items.append({
                "id": observation.id,
                "affected_asset": assets.get(observation.asset_id, "removed asset"),
                "status": observation.status.value,
                "observation": observation.observation,
                "inference": observation.inference,
                "severity_estimate": observation.severity,
                "confidence": observation.confidence,
                "source": observation.source,
                "detection_date": observation.detected_at.isoformat(),
                "false_positive_guidance": observation.false_positive_guidance,
                "missing_evidence": observation.missing_evidence,
                "it_verification_required": observation.it_verification_required,
                "evidence": [
                    {
                        "source_url": item.source_url,
                        "content_hash": item.content_hash,
                        "captured_at": item.captured_at.isoformat(),
                    }
                    for item in evidence
                ],
                "exploitation_claimed": False,
            })
        payload = {
            "report_type": "Aegis monthly public-information report",
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "limitations": [
                "Passive public-information monitoring only.",
                "No authentication, exploitation or protected-data access was performed.",
                "Potential indicators are not proof of vulnerability or compromise.",
                "Customer IT verification is required before remediation decisions.",
            ],
            "summary": {
                "observations": len(items),
                "potential_indicators": sum(
                    1 for item in items if item["status"] == "potential_indicator"
                ),
            },
            "items": items,
        }
        report = Report(
            workspace_id=workspace_id,
            period_start=period_start,
            period_end=period_end,
            status="ready",
            web_payload=payload,
        )
        self.session.add(report)
        return report
