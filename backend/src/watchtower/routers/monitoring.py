"""Monitoring page endpoints - real events from Log Analytics for APIM + Foundry.
No Watchtower internal audit here (that lives in /api/audit if needed)."""
from fastapi import APIRouter
from ..azure_clients import MonitorClient

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])


def _ws():
    m = MonitorClient()
    diag = m.check_apim_diagnostics()
    return m, diag.get("workspace_id")


@router.get("/summary")
async def summary(hours: int = 24):
    m, ws = _ws()
    if not ws:
        return {"hours": hours, "workspace_configured": False}
    s = m.query_apim_summary(ws, hours=hours)
    s["workspace_configured"] = True
    s["hours"] = hours
    return s


@router.get("/requests")
async def recent_requests(hours: int = 1, limit: int = 200):
    m, ws = _ws()
    if not ws:
        return {"hours": hours, "workspace_configured": False, "rows": []}
    return {
        "hours": hours,
        "workspace_configured": True,
        "rows": m.query_apim_recent_requests(ws, hours=hours, limit=limit),
    }


@router.get("/errors")
async def errors(hours: int = 24, limit: int = 200):
    m, ws = _ws()
    if not ws:
        return {"hours": hours, "workspace_configured": False, "rows": []}
    return {
        "hours": hours,
        "workspace_configured": True,
        "rows": m.query_apim_errors(ws, hours=hours, limit=limit),
    }


@router.get("/rate-limits")
async def rate_limits(hours: int = 24, limit: int = 200):
    m, ws = _ws()
    if not ws:
        return {"hours": hours, "workspace_configured": False, "rows": []}
    return {
        "hours": hours,
        "workspace_configured": True,
        "rows": m.query_apim_rate_limit_hits(ws, hours=hours, limit=limit),
    }


@router.get("/traffic")
async def traffic(hours: int = 24, bin_minutes: int = 15):
    m, ws = _ws()
    if not ws:
        return {"hours": hours, "workspace_configured": False, "rows": []}
    return {
        "hours": hours,
        "workspace_configured": True,
        "bin_minutes": bin_minutes,
        "rows": m.query_apim_traffic_timeline(ws, hours=hours, bin_minutes=bin_minutes),
    }


@router.get("/foundry")
async def foundry_events(hours: int = 24, limit: int = 100):
    """Foundry-side diagnostic logs. Returns empty if Foundry account doesn't have diagnostic
    settings enabled (that's a separate wire-up from APIM diagnostics)."""
    m, ws = _ws()
    if not ws:
        return {"hours": hours, "workspace_configured": False, "rows": []}
    return {
        "hours": hours,
        "workspace_configured": True,
        "rows": m.query_foundry_diagnostics(ws, hours=hours, limit=limit),
    }
