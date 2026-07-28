"""Professional PDF rendering for stored Aegis reports."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


CHARCOAL = colors.HexColor("#07100D")
PANEL = colors.HexColor("#EEF5F1")
EMERALD = colors.HexColor("#0E8F62")
MUTED = colors.HexColor("#52665D")
LINE = colors.HexColor("#D6E2DC")
FONT_DIR = Path(reportlab.__file__).resolve().parent / "fonts"
pdfmetrics.registerFont(TTFont("AegisSans", str(FONT_DIR / "Vera.ttf")))
pdfmetrics.registerFont(TTFont("AegisSans-Bold", str(FONT_DIR / "VeraBd.ttf")))
pdfmetrics.registerFontFamily(
    "AegisSans",
    normal="AegisSans",
    bold="AegisSans-Bold",
    italic="AegisSans",
    boldItalic="AegisSans-Bold",
)


def _safe(value) -> str:
    text = str(value if value is not None else "")
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", "-")
    )


def build_report_pdf(company_name: str, payload: dict) -> bytes:
    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=19 * mm,
        rightMargin=19 * mm,
        topMargin=24 * mm,
        bottomMargin=18 * mm,
        title=f"Aegis monthly report - {company_name}",
        author="Aegis",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="report")

    def page(canvas, document):
        canvas.saveState()
        canvas.setFillColor(CHARCOAL)
        canvas.rect(0, A4[1] - 15 * mm, A4[0], 15 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("AegisSans-Bold", 12)
        canvas.drawString(19 * mm, A4[1] - 10 * mm, "AEGIS")
        canvas.setFillColor(MUTED)
        canvas.setFont("AegisSans", 8)
        canvas.drawString(19 * mm, 9 * mm, "Passive public-information monitoring")
        canvas.drawRightString(A4[0] - 19 * mm, 9 * mm, f"Page {document.page}")
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id="aegis", frames=[frame], onPage=page)])
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "Title", parent=styles["Title"], fontName="AegisSans-Bold", fontSize=25,
        leading=29, textColor=CHARCOAL, alignment=0, spaceAfter=7 * mm,
    )
    h2 = ParagraphStyle(
        "H2", parent=styles["Heading2"], fontName="AegisSans-Bold", fontSize=15,
        leading=19, textColor=CHARCOAL, spaceBefore=6 * mm, spaceAfter=3 * mm,
    )
    body = ParagraphStyle(
        "Body", parent=styles["BodyText"], fontName="AegisSans", fontSize=9.5,
        leading=14, textColor=MUTED, spaceAfter=2.5 * mm,
    )
    label = ParagraphStyle(
        "Label", parent=body, fontName="AegisSans-Bold", fontSize=8,
        leading=10, textColor=EMERALD, uppercase=True,
    )
    small_right = ParagraphStyle(
        "SmallRight", parent=body, fontSize=8, alignment=TA_RIGHT,
    )
    story = [
        Spacer(1, 4 * mm),
        Paragraph("Monthly public-information report", title),
        Table(
            [
                [Paragraph("<b>Customer</b>", body), Paragraph(_safe(company_name), small_right)],
                [Paragraph("<b>Period</b>", body), Paragraph(
                    f"{_safe(payload.get('period_start', ''))[:10]} to "
                    f"{_safe(payload.get('period_end', ''))[:10]}", small_right
                )],
            ],
            colWidths=[45 * mm, 110 * mm],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                ("BOX", (0, 0), (-1, -1), .5, LINE),
                ("INNERGRID", (0, 0), (-1, -1), .3, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]),
        ),
        Paragraph("Executive summary", h2),
        Paragraph(
            f"Aegis recorded <b>{int((payload.get('summary') or {}).get('observations', 0))}</b> "
            f"public observations and <b>{int((payload.get('summary') or {}).get('potential_indicators', 0))}</b> "
            "potential indicators during this period. Potential indicators are not proof of "
            "vulnerability, exploitation or compromise.", body,
        ),
        Paragraph("Service limitations", h2),
    ]
    for limitation in payload.get("limitations") or []:
        story.append(Paragraph(f"- {_safe(limitation)}", body))

    story.append(Paragraph("Observations and potential indicators", h2))
    items = payload.get("items") or []
    if not items:
        story.append(Paragraph("No observations were recorded in this reporting period.", body))
    for index, item in enumerate(items, 1):
        evidence = item.get("evidence") or []
        story.extend([
            Table(
                [[
                    Paragraph(f"<b>{index}. {_safe(item.get('affected_asset'))}</b>", body),
                    Paragraph(
                        f"{_safe(item.get('status'))} | "
                        f"{_safe(item.get('severity_estimate'))} | "
                        f"{_safe(item.get('confidence'))} confidence",
                        small_right,
                    ),
                ]],
                colWidths=[78 * mm, 77 * mm],
                style=TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                    ("BOX", (0, 0), (-1, -1), .5, LINE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]),
            ),
            Spacer(1, 2 * mm),
            Paragraph("OBSERVATION", label),
            Paragraph(_safe(item.get("observation")), body),
            Paragraph("INFERENCE", label),
            Paragraph(_safe(item.get("inference") or "No inference recorded."), body),
            Paragraph("SOURCE AND DATE", label),
            Paragraph(
                f"{_safe(item.get('source'))}<br/>{_safe(item.get('detection_date'))}", body,
            ),
            Paragraph("VERIFICATION GUIDANCE", label),
            Paragraph(
                _safe(item.get("false_positive_guidance") or "Customer IT verification required."),
                body,
            ),
            Paragraph("MISSING EVIDENCE", label),
            Paragraph(_safe(item.get("missing_evidence") or "Not specified."), body),
            Paragraph(
                f"<b>Evidence records:</b> {len(evidence)}. "
                "Aegis has not claimed exploitation.", body,
            ),
            Spacer(1, 4 * mm),
        ])
    story.extend([
        Paragraph("Required next step", h2),
        Paragraph(
            "Provide this report to the person responsible for the affected systems. "
            "They should verify the current configuration and product versions before "
            "making a remediation decision.", body,
        ),
    ])
    doc.build(story)
    return buffer.getvalue()
