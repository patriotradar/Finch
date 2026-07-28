"""Accessible customer web rendering for Aegis reports."""

from html import escape


def render_report_html(company_name: str, payload: dict) -> str:
    summary = payload.get("summary") or {}
    items = []
    for item in payload.get("items") or []:
        items.append(f"""
        <article>
          <div class="top"><h2>{escape(str(item.get("affected_asset") or "Asset"))}</h2>
          <span>{escape(str(item.get("status") or "observation"))}</span></div>
          <dl>
            <dt>Observation</dt><dd>{escape(str(item.get("observation") or ""))}</dd>
            <dt>Inference</dt><dd>{escape(str(item.get("inference") or "No inference recorded."))}</dd>
            <dt>Severity estimate</dt><dd>{escape(str(item.get("severity_estimate") or "unknown"))}</dd>
            <dt>Confidence</dt><dd>{escape(str(item.get("confidence") or "unknown"))}</dd>
            <dt>Source</dt><dd>{escape(str(item.get("source") or ""))}</dd>
            <dt>Detection date</dt><dd>{escape(str(item.get("detection_date") or ""))}</dd>
            <dt>Verification guidance</dt><dd>{escape(str(item.get("false_positive_guidance") or ""))}</dd>
            <dt>Missing evidence</dt><dd>{escape(str(item.get("missing_evidence") or ""))}</dd>
          </dl>
          <p class="verify">IT verification required. Aegis has not claimed exploitation.</p>
        </article>""")
    content = "".join(items) or "<p>No observations were recorded in this period.</p>"
    limitations = "".join(
        f"<li>{escape(str(item))}</li>" for item in payload.get("limitations") or []
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Aegis report - {escape(company_name)}</title><style>
    :root{{--bg:#07100d;--panel:#0e1915;--line:#263b32;--text:#edf7f2;--muted:#a2b5ac;--green:#34d399}}
    *{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 system-ui,sans-serif}}
    header,main{{max-width:900px;margin:auto;padding:28px 22px}}header{{border-bottom:1px solid var(--line)}}
    h1{{font-size:36px;line-height:1.1;margin:25px 0 8px}}p,dd,li{{color:var(--muted)}}.summary{{display:flex;gap:14px;flex-wrap:wrap;margin:25px 0}}
    .stat,article,.limits{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px}}.stat b{{font-size:28px;color:var(--green)}}
    article{{margin:14px 0}}.top{{display:flex;justify-content:space-between;gap:15px;align-items:center}}.top span{{color:var(--green)}}
    dt{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--green);font-weight:800;margin-top:13px}}dd{{margin:3px 0}}.verify{{border-top:1px solid var(--line);padding-top:12px}}a{{color:var(--green)}}
    </style></head><body><header><a href="/account">Aegis customer workspace</a></header><main>
    <h1>Monthly public-information report</h1><p>{escape(company_name)} · {escape(str(payload.get("period_start") or ""))[:10]} to {escape(str(payload.get("period_end") or ""))[:10]}</p>
    <div class="summary"><div class="stat"><b>{int(summary.get("observations",0))}</b><br>Observations</div><div class="stat"><b>{int(summary.get("potential_indicators",0))}</b><br>Potential indicators</div></div>
    <section class="limits"><h2>Limitations</h2><ul>{limitations}</ul></section>
    <h2>Evidence-led results</h2>{content}</main></body></html>"""
