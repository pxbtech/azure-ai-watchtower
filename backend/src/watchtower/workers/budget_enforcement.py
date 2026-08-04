"""Budget enforcement worker.

Runs as a background asyncio task from the FastAPI lifespan.
Every INTERVAL_SECONDS: pulls token counts per governed deployment from App Insights,
applies published per-token pricing, compares to monthly_budget_usd, and auto-suspends
via the existing three-layer suspend path when cost >= budget * threshold_pct / 100.

Deployments with:
  - no monthly_budget_usd set     -> skipped (nothing to enforce)
  - unknown model pricing         -> skipped (can't compute cost honestly)
  - state already 'suspended'     -> skipped (whether from prior auto or manual)
  - no LA workspace on APIM diag  -> skipped globally (no data source)
"""
import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy import select

from ..db import AsyncSessionLocal
from ..models import DeploymentRecord, SuspensionRecord, AuditLog
from ..azure_clients import MonitorClient, ApimClient
from ..routers.billing import _price_lookup, _estimated_cost

log = logging.getLogger("watchtower.budget")
INTERVAL_SECONDS = 60  # tight loop; App Insights ingestion latency dominates anyway

_task: asyncio.Task | None = None


def _month_to_date_days() -> int:
    """Return days elapsed in the current UTC calendar month, minimum 1."""
    now = datetime.now(timezone.utc)
    return max(now.day, 1)


async def compute_status_for(record: DeploymentRecord, monitor: MonitorClient, workspace_id: str | None) -> dict:
    """Return the current billing status for one deployment. Read-only, no side effects."""
    status = {
        "deployment_name": record.deployment_name,
        "monthly_budget_usd": record.monthly_budget_usd,
        "threshold_pct": record.threshold_pct,
        "mtd_prompt_tokens": 0,
        "mtd_completion_tokens": 0,
        "mtd_cost_usd": None,
        "burn_pct": None,
        "threshold_cost_usd": None,
        "over_threshold": False,
        "skip_reason": None,
    }
    if not record.monthly_budget_usd or record.monthly_budget_usd <= 0:
        status["skip_reason"] = "no budget set"
        return status
    price = _price_lookup(record.model_name, sku_hint=record.sku_name)
    if price is None:
        status["skip_reason"] = f"no pricing found in Azure Retail Prices API for model '{record.model_name}' sku '{record.sku_name}' in region '{monitor.__class__.__name__}'"
        return status
    if not workspace_id:
        status["skip_reason"] = "APIM diagnostics has no LA workspace"
        return status

    days = _month_to_date_days()
    tokens = monitor.query_tokens_by_deployment(workspace_id, days=days)
    d = tokens.get(record.deployment_name, {})
    prompt = d.get("prompt", 0)
    completion = d.get("completion", 0)
    cost = _estimated_cost(record.model_name, prompt, completion)

    status["mtd_prompt_tokens"] = prompt
    status["mtd_completion_tokens"] = completion
    status["mtd_cost_usd"] = round(cost, 4) if cost is not None else None
    if cost is not None and record.monthly_budget_usd:
        burn = 100.0 * cost / record.monthly_budget_usd
        threshold_cost = record.monthly_budget_usd * (record.threshold_pct / 100.0)
        status["burn_pct"] = round(burn, 2)
        status["threshold_cost_usd"] = round(threshold_cost, 4)
        status["over_threshold"] = cost >= threshold_cost
    return status


async def _auto_suspend(record: DeploymentRecord, cost: float, apim: ApimClient) -> tuple[bool, str]:
    """Perform three-layer suspend (Layer 1 named value, Layer 2 subscription) and record it.
    Returns (success, reason_message)."""
    reason = (
        f"auto-suspended by budget enforcer: MTD cost ${cost:.2f} "
        f"crossed {record.threshold_pct}% of ${record.monthly_budget_usd:.2f} budget"
    )
    layers = {"named_value": False, "subscription": False, "auto_budget": True}
    try:
        apim.add_to_suspended_csv(record.deployment_name)
        layers["named_value"] = True
    except Exception as e:
        layers["named_value_error"] = str(e)
    if record.apim_subscription_id:
        try:
            apim.set_subscription_state(record.apim_subscription_id, "suspended")
            layers["subscription"] = True
        except Exception as e:
            layers["subscription_error"] = str(e)

    async with AsyncSessionLocal() as s:
        result = await s.execute(select(DeploymentRecord).where(DeploymentRecord.deployment_name == record.deployment_name))
        fresh = result.scalar_one_or_none()
        if fresh is None:
            return False, "deployment record disappeared mid-suspend"
        fresh.state = "suspended"
        s.add(SuspensionRecord(
            deployment_name=record.deployment_name,
            action="suspend",
            layers_applied=layers,
            actor="watchtower-budget-enforcer",
            reason=reason,
        ))
        s.add(AuditLog(
            actor="watchtower-budget-enforcer",
            action="deployment.auto-suspend",
            target_type="deployment",
            target_id=record.deployment_name,
            after={
                "mtd_cost_usd": round(cost, 4),
                "monthly_budget_usd": record.monthly_budget_usd,
                "threshold_pct": record.threshold_pct,
                "layers": layers,
            },
        ))
        await s.commit()
    return True, reason


async def run_once() -> dict:
    """Single pass. Returns summary. Safe to call from an HTTP endpoint for manual triggers."""
    monitor = MonitorClient()
    apim = ApimClient()
    diag = monitor.check_apim_diagnostics()
    workspace_id = diag.get("workspace_id")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(DeploymentRecord).where(DeploymentRecord.state == "active"))
        active = list(result.scalars().all())

    checked = 0
    suspended: list[dict] = []
    for record in active:
        status = await compute_status_for(record, monitor, workspace_id)
        if status.get("skip_reason"):
            continue
        checked += 1
        if status["over_threshold"] and status["mtd_cost_usd"] is not None:
            ok, reason = await _auto_suspend(record, status["mtd_cost_usd"], apim)
            suspended.append({
                "deployment_name": record.deployment_name,
                "cost_usd": status["mtd_cost_usd"],
                "budget_usd": record.monthly_budget_usd,
                "threshold_pct": record.threshold_pct,
                "ok": ok,
                "reason": reason,
            })
            log.warning("budget suspended %s: %s", record.deployment_name, reason)

    return {
        "workspace_configured": workspace_id is not None,
        "active_governed_deployments": len(active),
        "checked": checked,
        "auto_suspended": suspended,
        "at": datetime.now(timezone.utc).isoformat(),
    }


async def _loop():
    log.info("Budget enforcer loop starting, interval=%ss", INTERVAL_SECONDS)
    while True:
        try:
            summary = await run_once()
            if summary["auto_suspended"]:
                log.warning("auto-suspend cycle: %s", summary)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("budget enforcer loop error: %s", e)
        await asyncio.sleep(INTERVAL_SECONDS)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except Exception:
            pass
        _task = None
