from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..db import get_db
from ..models import AuditLog, SuspensionRecord

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
async def list_audit(limit: int = 100, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AuditLog).order_by(AuditLog.at.desc()).limit(limit)
    )
    rows = result.scalars().all()
    return [{
        "id": r.id, "actor": r.actor, "action": r.action,
        "target_type": r.target_type, "target_id": r.target_id,
        "before": r.before, "after": r.after,
        "at": r.at.isoformat(),
    } for r in rows]


@router.get("/suspensions")
async def list_suspensions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SuspensionRecord).order_by(SuspensionRecord.at.desc()).limit(100))
    rows = result.scalars().all()
    return [{
        "id": r.id, "deployment_name": r.deployment_name, "action": r.action,
        "layers_applied": r.layers_applied, "actor": r.actor, "reason": r.reason,
        "at": r.at.isoformat(),
    } for r in rows]
