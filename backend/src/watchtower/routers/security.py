from fastapi import APIRouter, HTTPException
from ..azure_clients import MonitorClient

router = APIRouter(prefix="/api/security", tags=["security"])


def _workspace():
    m = MonitorClient()
    diag = m.check_apim_diagnostics()
    return m, diag.get("workspace_id")


@router.get("/blocked-content")
async def blocked_content(hours: int = 24):
    m, ws = _workspace()
    if not ws:
        # Empty state, not an error
        return {"hours": hours, "workspace_configured": False, "rows": []}
    try:
        return {"hours": hours, "workspace_configured": True, "rows": m.query_blocked_content(ws, hours=hours)}
    except Exception as e:
        raise HTTPException(500, f"Blocked content query failed: {e}")


@router.get("/jailbreak")
async def jailbreak(hours: int = 24):
    m, ws = _workspace()
    if not ws:
        return {"hours": hours, "workspace_configured": False, "rows": []}
    try:
        return {"hours": hours, "workspace_configured": True, "rows": m.query_jailbreak_attempts(ws, hours=hours)}
    except Exception as e:
        raise HTTPException(500, f"Jailbreak query failed: {e}")


@router.get("/config-drift")
async def config_drift(hours: int = 168):
    """Configuration change events on the target Foundry account.
    Sources from Azure Activity Log, not APIM logs, so it works even without LA setup."""
    m = MonitorClient()
    try:
        return {"hours": hours, "rows": m.query_config_drift(hours=hours)}
    except Exception as e:
        raise HTTPException(500, f"Config drift query failed: {e}")


@router.get("/summary")
async def summary(hours: int = 24):
    """Aggregated counts for the Security page header tiles."""
    m, ws = _workspace()
    blocked = 0
    jb = 0
    if ws:
        try:
            for r in m.query_blocked_content(ws, hours=hours):
                blocked += int(r.get("blocks") or 0)
        except Exception:
            pass
        try:
            jb = len(m.query_jailbreak_attempts(ws, hours=hours))
        except Exception:
            pass
    try:
        drift_count = len(m.query_config_drift(hours=hours))
    except Exception:
        drift_count = 0
    return {
        "hours": hours,
        "workspace_configured": ws is not None,
        "blocked_content_events": blocked,
        "jailbreak_attempts": jb,
        "config_drift_events": drift_count,
    }
