"""Monthly billing - real per-endpoint cost from App Insights token metric.
No dummy data. If no traffic has hit the endpoint, cost shows as null."""
import csv
import io
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..azure_clients import CostClient, MonitorClient, settings as azure_settings
from ..db import get_db
from ..models import DeploymentRecord
from .. import retail_prices

router = APIRouter(prefix="/api/billing", tags=["billing"])


# Per-1M-tokens USD price lookup for common OpenAI models (public retail rates).
# Kept small on purpose - if a model isn't in this table, cost shows as null and the UI
# flags it as "no pricing available" rather than guessing.
MODEL_PRICING_PER_1M = {
    "gpt-4o":              {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":         {"input": 0.15,  "output": 0.60},
    "gpt-4-turbo":         {"input": 10.00, "output": 30.00},
    "gpt-4":               {"input": 30.00, "output": 60.00},
    "gpt-35-turbo":        {"input": 0.50,  "output": 1.50},
    "gpt-3.5-turbo":       {"input": 0.50,  "output": 1.50},
    "o1":                  {"input": 15.00, "output": 60.00},
    "o1-mini":             {"input": 3.00,  "output": 12.00},
    "o3-mini":             {"input": 1.10,  "output": 4.40},
    "text-embedding-3-small": {"input": 0.02, "output": 0.00},
    "text-embedding-3-large": {"input": 0.13, "output": 0.00},
    "text-embedding-ada-002": {"input": 0.10, "output": 0.00},
}


def _price_lookup(model_name: str, region: str | None = None, sku_hint: str | None = None) -> dict | None:
    """Return per-1M-tokens USD {input, output}.
    Hardcoded table wins for known models (predictable, exact, well-tested).
    Retail Prices API is the fallback for anything not in the table (any future model)."""
    if not model_name:
        return None

    # 1. Try hardcoded for known common models - exact match first
    key = model_name.lower()
    if key in MODEL_PRICING_PER_1M:
        return MODEL_PRICING_PER_1M[key]
    for prefix, price in MODEL_PRICING_PER_1M.items():
        if key.startswith(prefix):
            return price

    # 2. Retail Prices API for anything else (unknown / future models)
    reg = region or azure_settings.location
    live = retail_prices.get_pricing_per_1m(model_name, reg, sku_hint)
    if live:
        return {"input": live["input"], "output": live["output"]}
    return None


@router.get("/pricing/lookup")
async def pricing_lookup(model: str, region: str | None = None, sku: str | None = None):
    """Debug endpoint: what does Retail Prices API return for this model?"""
    reg = region or azure_settings.location
    # Clear cache first so we always see fresh result during debugging
    retail_prices.clear_cache()
    live = retail_prices.get_pricing_per_1m(model, reg, sku)
    fallback = None
    key = model.lower()
    if key in MODEL_PRICING_PER_1M:
        fallback = MODEL_PRICING_PER_1M[key]
    return {"model": model, "region": reg, "sku_hint": sku, "retail_api": live, "hardcoded_fallback": fallback}


@router.get("/pricing/cache")
async def pricing_cache():
    return {"entries": retail_prices.cache_snapshot()}


@router.post("/pricing/cache/clear")
async def pricing_cache_clear():
    n = retail_prices.clear_cache()
    return {"cleared_entries": n}


def _estimated_cost(model_name: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    price = _price_lookup(model_name)
    if price is None:
        return None
    return round(
        (prompt_tokens / 1_000_000) * price["input"]
        + (completion_tokens / 1_000_000) * price["output"],
        4,
    )


@router.get("/by-endpoint")
async def by_endpoint(days: int = 30, db: AsyncSession = Depends(get_db)):
    """Real per-endpoint cost. Queries App Insights token metric (emitted by
    azure-openai-emit-token-metric policy). Returns null cost for endpoints with
    no traffic or no known pricing - NEVER fabricates numbers."""
    result = await db.execute(select(DeploymentRecord))
    records = list(result.scalars().all())

    monitor = MonitorClient()
    diag = monitor.check_apim_diagnostics()
    workspace_id = diag.get("workspace_id")

    token_data: dict = {}
    if workspace_id:
        token_data = monitor.query_tokens_by_deployment(workspace_id, days=days)

    rows = []
    for r in records:
        tokens = token_data.get(r.deployment_name, {})
        prompt_t = tokens.get("prompt", 0)
        completion_t = tokens.get("completion", 0)
        total_t = tokens.get("total", 0) or (prompt_t + completion_t)

        price = _price_lookup(r.model_name, sku_hint=r.sku_name)
        cost_usd = None
        if total_t > 0 and price is not None:
            cost_usd = round((prompt_t / 1_000_000) * price["input"] + (completion_t / 1_000_000) * price["output"], 4)

        rows.append({
            "deployment_name": r.deployment_name,
            "project_name": r.project_name,
            "app_name": r.app_name,
            "app_team": r.app_team,
            "business_unit": r.business_unit,
            "environment": r.environment,
            "cost_center": r.cost_center,
            "model": f"{r.model_name}@{r.model_version}",
            "model_name": r.model_name,
            "monthly_budget_usd": r.monthly_budget_usd,
            "tpm_limit": r.tpm_limit,
            "throttling_rpm": r.throttling_rpm,
            "prompt_tokens": prompt_t,
            "completion_tokens": completion_t,
            "total_tokens": total_t,
            "estimated_cost_usd": cost_usd,
            "has_traffic": total_t > 0,
            "has_pricing": price is not None,
        })

    return {
        "days": days,
        "workspace_configured": workspace_id is not None,
        "endpoints": rows,
        "pricing_note": "Estimated cost = tokens × published retail per-1M-token rate. See MODEL_PRICING_PER_1M in backend/src/watchtower/routers/billing.py.",
    }


@router.get("/by-endpoint.csv")
async def by_endpoint_csv(days: int = 30, db: AsyncSession = Depends(get_db)):
    payload = await by_endpoint(days=days, db=db)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"AI Watchtower per-endpoint bill - last {days} days"])
    w.writerow([f"Foundry: {azure_settings.foundry_account}"])
    w.writerow([f"Generated: {datetime.now(timezone.utc).isoformat()}"])
    w.writerow([f"Workspace configured: {payload['workspace_configured']}"])
    w.writerow([])
    w.writerow(["Endpoint", "Project", "App", "Owner-Team", "Business unit", "Env", "Cost center",
                "Model", "Monthly budget (USD)", "TPM", "RPM",
                "Prompt tokens", "Completion tokens", "Total tokens", "Est cost (USD)"])
    for e in payload["endpoints"]:
        w.writerow([
            e["deployment_name"], e.get("project_name") or "",
            e["app_name"], e.get("app_team") or "",
            e.get("business_unit") or "", e.get("environment") or "",
            e.get("cost_center") or "",
            e["model"],
            e.get("monthly_budget_usd") if e.get("monthly_budget_usd") is not None else "",
            e["tpm_limit"], e["throttling_rpm"],
            e["prompt_tokens"], e["completion_tokens"], e["total_tokens"],
            e["estimated_cost_usd"] if e["estimated_cost_usd"] is not None else "",
        ])

    buf.seek(0)
    filename = f"watchtower-endpoints-{datetime.now(timezone.utc):%Y-%m-%d}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/endpoint/{deployment_name}/pdf")
async def endpoint_pdf(deployment_name: str, days: int = 30, db: AsyncSession = Depends(get_db)):
    """Downloadable PDF: monthly consumption for a single endpoint, broken by day."""
    result = await db.execute(select(DeploymentRecord).where(DeploymentRecord.deployment_name == deployment_name))
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(404, f"Deployment '{deployment_name}' not tracked by Watchtower")

    monitor = MonitorClient()
    diag = monitor.check_apim_diagnostics()
    workspace_id = diag.get("workspace_id")
    daily = monitor.query_tokens_by_day(workspace_id, deployment_name, days=days) if workspace_id else []

    price = _price_lookup(record.model_name)
    total_prompt = sum(d.get("prompt", 0) for d in daily)
    total_completion = sum(d.get("completion", 0) for d in daily)
    total_cost = _estimated_cost(record.model_name, total_prompt, total_completion) if price else None

    # Render PDF with reportlab
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.6 * inch, rightMargin=0.6 * inch, topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("t", parent=styles["Title"], fontSize=18, spaceAfter=6)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12, spaceAfter=6, textColor=colors.HexColor("#344767"))
    body = ParagraphStyle("b", parent=styles["BodyText"], fontSize=9.5, textColor=colors.HexColor("#344767"))

    story = []
    story.append(Paragraph("AI Watchtower - Endpoint Consumption Report", title))
    story.append(Paragraph(f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", body))
    story.append(Spacer(1, 12))

    meta = [
        ["Endpoint", record.deployment_name],
        ["Project", record.project_name or "-"],
        ["App", f"{record.app_name} ({record.environment or '-'})"],
        ["Owner", record.app_owner or "-"],
        ["Team", record.app_team or "-"],
        ["Business unit", record.business_unit or "-"],
        ["Cost center", record.cost_center or "-"],
        ["Model", f"{record.model_name}@{record.model_version} / {record.sku_name}"],
        ["Period", f"Last {days} days"],
        ["Rate limit", f"{record.tpm_limit:,} tok/min, {record.throttling_rpm} req/min"],
        ["Monthly budget", f"${record.monthly_budget_usd:,.2f} USD" if record.monthly_budget_usd else "not set"],
    ]
    meta_tbl = Table(meta, colWidths=[1.6 * inch, 5.2 * inch])
    meta_tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#67748e")),
        ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor("#344767")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#edf0f5")),
    ]))
    story.append(meta_tbl)
    story.append(Spacer(1, 14))
    story.append(Paragraph("Daily consumption", h2))

    if not workspace_id:
        story.append(Paragraph(
            "Log Analytics workspace is not configured on APIM diagnostics. "
            "Per-day token metrics cannot be queried. Fix diagnostics to populate this section.",
            body,
        ))
    elif not daily:
        story.append(Paragraph(f"No traffic recorded in the last {days} days.", body))
    else:
        header = ["Day", "Prompt tokens", "Completion tokens", "Total tokens", "Est. cost (USD)"]
        rows = [header]
        for d in daily:
            day_cost = _estimated_cost(record.model_name, d.get("prompt", 0), d.get("completion", 0))
            rows.append([
                d["day"],
                f"{d.get('prompt', 0):,}",
                f"{d.get('completion', 0):,}",
                f"{(d.get('total') or d.get('prompt', 0) + d.get('completion', 0)):,}",
                f"${day_cost:.4f}" if day_cost is not None else "n/a",
            ])
        rows.append([
            "TOTAL",
            f"{total_prompt:,}",
            f"{total_completion:,}",
            f"{total_prompt + total_completion:,}",
            f"${total_cost:.2f}" if total_cost is not None else "n/a",
        ])
        tbl = Table(rows, colWidths=[1.4 * inch, 1.4 * inch, 1.6 * inch, 1.3 * inch, 1.3 * inch])
        tbl.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -2), "Helvetica"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#fafbfc")),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f0f2f5")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#67748e")),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("LINEBELOW", (0, 0), (-1, 0), 0.4, colors.HexColor("#dee2ea")),
            ("LINEABOVE", (0, -1), (-1, -1), 0.4, colors.HexColor("#dee2ea")),
        ]))
        story.append(tbl)

    story.append(Spacer(1, 20))
    story.append(Paragraph(
        "Cost is estimated using published OpenAI retail rates for the deployed model. "
        "Actual Azure billing may differ (region, discounts, taxes). Reconcile against the "
        "Foundry account total in Cost Management.",
        body,
    ))

    doc.build(story)
    buf.seek(0)
    filename = f"watchtower-{deployment_name}-{datetime.now(timezone.utc):%Y-%m-%d}.pdf"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/foundry-total")
async def foundry_total(year: int | None = None, month: int | None = None):
    """Azure Cost Management total for the whole Foundry account (context, not per-endpoint).
    Shown as sanity-check reconciliation, not as chargeback data."""
    if year is None or month is None:
        now = datetime.now(timezone.utc)
        year, month = now.year, now.month
    lines = CostClient().monthly_by_meter(year, month)
    total = sum(float(r.get("PreTaxCost") or 0) for r in lines)
    return {
        "year": year, "month": month,
        "foundry_account": azure_settings.foundry_account,
        "total_usd": round(total, 2),
        "line_count": len(lines),
    }
