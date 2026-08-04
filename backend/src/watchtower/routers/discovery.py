from fastapi import APIRouter, HTTPException
from ..azure_clients import DiscoveryClient, FoundryClient, ApimClient
from ..config import get_settings

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


@router.get("/resources")
async def list_resources():
    try:
        return DiscoveryClient().resources()
    except Exception as e:
        raise HTTPException(500, f"Resource Graph query failed: {e}")


@router.get("/summary")
async def summary():
    """Snapshot of what AI Watchtower can see in the target subscription."""
    s = get_settings()
    try:
        resources = DiscoveryClient().resources()
    except Exception as e:
        raise HTTPException(500, f"Discovery failed: {e}")

    by_type: dict[str, list] = {}
    for r in resources:
        by_type.setdefault(r["type"], []).append(r)

    return {
        "subscription_id": s.subscription_id,
        "resource_group": s.resource_group,
        "location": s.location,
        "target_foundry": s.foundry_account,
        "target_apim": s.apim_service,
        "target_key_vault": s.key_vault,
        "resources_by_type": by_type,
        "total_resources": len(resources),
    }
