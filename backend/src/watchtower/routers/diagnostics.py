from fastapi import APIRouter, HTTPException
from ..azure_clients import MonitorClient, ApimClient

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("/apim")
async def apim_diagnostics():
    """Checks the target APIM's diagnostic settings. Blocks progression if incorrect."""
    try:
        return MonitorClient().check_apim_diagnostics()
    except Exception as e:
        raise HTTPException(500, f"Diagnostics check failed: {e}")


@router.get("/apim/service")
async def apim_service():
    try:
        return ApimClient().get_service()
    except Exception as e:
        raise HTTPException(500, f"APIM read failed: {e}")
