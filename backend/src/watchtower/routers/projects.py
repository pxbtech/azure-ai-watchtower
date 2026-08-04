"""Projects: hierarchical grouping of Watchtower-governed deployments by project_name."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..azure_clients import ApimClient, FoundryClient, MonitorClient
from ..db import get_db
from ..models import DeploymentRecord
from .details import _score_deployment, _parse_policies_applied
from .billing import _price_lookup, _estimated_cost

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _dep_to_dict(r: DeploymentRecord) -> dict:
    return {
        "deployment_name": r.deployment_name,
        "project_name": r.project_name,
        "model_name": r.model_name,
        "model_version": r.model_version,
        "sku_name": r.sku_name,
        "capacity": r.capacity,
        "app_name": r.app_name,
        "app_owner": r.app_owner,
        "app_team": r.app_team,
        "business_unit": r.business_unit,
        "environment": r.environment,
        "cost_center": r.cost_center,
        "use_case_description": r.use_case_description,
        "tpm_limit": r.tpm_limit,
        "throttling_rpm": r.throttling_rpm,
        "monthly_budget_usd": r.monthly_budget_usd,
        "threshold_pct": r.threshold_pct,
        "apim_api_id": r.apim_api_id,
        "apim_subscription_id": r.apim_subscription_id,
        "kv_secret_uri": r.kv_secret_uri,
        "state": r.state,
        "created_at": r.created_at.isoformat(),
    }


@router.get("")
async def list_projects(db: AsyncSession = Depends(get_db)):
    """Return projects grouped by project_name (or 'unassigned') with deployments nested.
    Enriches each deployment with real MTD cost + burn% (single LA query batched across all)."""
    result = await db.execute(select(DeploymentRecord).order_by(DeploymentRecord.created_at.desc()))
    records = list(result.scalars().all())

    # Batch-fetch tokens for ALL deployments in one KQL query (uses dimension filter server-side)
    tokens_by_dep: dict = {}
    workspace_id = None
    try:
        monitor = MonitorClient()
        diag = monitor.check_apim_diagnostics()
        workspace_id = diag.get("workspace_id")
        if workspace_id:
            days = max(datetime.now(timezone.utc).day, 1)
            tokens_by_dep = monitor.query_tokens_by_deployment(workspace_id, days=days)
    except Exception:
        pass

    projects: dict[str, dict] = {}
    for r in records:
        key = r.project_name or "unassigned"
        if key not in projects:
            projects[key] = {
                "project_name": key, "deployments": [],
                "business_unit": r.business_unit, "app_team": r.app_team, "environment": r.environment,
            }

        d = _dep_to_dict(r)
        # Enrich with cost
        tk = tokens_by_dep.get(r.deployment_name, {})
        prompt = tk.get("prompt", 0)
        completion = tk.get("completion", 0)
        total_tokens = tk.get("total", 0) or (prompt + completion)
        price = _price_lookup(r.model_name, sku_hint=r.sku_name)
        cost_usd = None
        burn_pct = None
        if price is not None and total_tokens > 0:
            cost_usd = round((prompt / 1_000_000) * price["input"] + (completion / 1_000_000) * price["output"], 4)
            if r.monthly_budget_usd and r.monthly_budget_usd > 0:
                burn_pct = round(100 * cost_usd / r.monthly_budget_usd, 1)
        d["mtd_prompt_tokens"] = prompt
        d["mtd_completion_tokens"] = completion
        d["mtd_total_tokens"] = total_tokens
        d["mtd_cost_usd"] = cost_usd
        d["burn_pct"] = burn_pct
        d["has_pricing"] = price is not None
        projects[key]["deployments"].append(d)

    for p in projects.values():
        p["deployment_count"] = len(p["deployments"])
        p["monthly_budget_usd"] = sum(d["monthly_budget_usd"] or 0 for d in p["deployments"])
        p["active_count"] = sum(1 for d in p["deployments"] if d["state"] == "active")
        p["mtd_cost_usd"] = round(sum(d.get("mtd_cost_usd") or 0 for d in p["deployments"]), 4)

    return {"projects": list(projects.values()), "workspace_configured": workspace_id is not None}


@router.delete("/{project_name}")
async def delete_project(project_name: str, db: AsyncSession = Depends(get_db)):
    """Delete a Foundry project. REFUSES if any Watchtower-governed deployments still reference it.
    Enforces: no project deletion while endpoints live under it."""
    from fastapi import HTTPException
    from ..azure_clients import FoundryClient
    from ..models import AuditLog

    # Count deployments referencing this project
    result = await db.execute(select(DeploymentRecord).where(DeploymentRecord.project_name == project_name))
    remaining = result.scalars().all()
    if remaining:
        names = [r.deployment_name for r in remaining]
        raise HTTPException(
            409,
            f"Cannot delete project '{project_name}': {len(names)} deployment(s) still exist "
            f"({', '.join(names[:5])}{'...' if len(names) > 5 else ''}). Delete all deployments first."
        )

    ok, msg = FoundryClient().delete_project(project_name)
    if not ok:
        raise HTTPException(500, f"Foundry project delete failed: {msg}")

    db.add(AuditLog(
        actor="watchtower-ui",
        action="project.delete",
        target_type="project",
        target_id=project_name,
        after={"foundry_result": msg},
    ))
    await db.commit()
    return {"project_name": project_name, "deleted": True, "detail": msg}


@router.get("/{project_name}/compliance")
async def project_compliance(project_name: str, db: AsyncSession = Depends(get_db)):
    """Compliance grades for every deployment in a project."""
    result = await db.execute(
        select(DeploymentRecord).where(DeploymentRecord.project_name == project_name)
    )
    records = list(result.scalars().all())

    foundry = FoundryClient()
    apim = ApimClient()
    account = {}
    try:
        account = foundry.get_account()
    except Exception:
        pass

    rows = []
    for r in records:
        policy_xml = apim.get_api_policy(r.apim_api_id) if r.apim_api_id else None
        sub_state = apim.get_subscription_state(r.apim_subscription_id) if r.apim_subscription_id else None
        score = _score_deployment(r, account, policy_xml, sub_state)
        rows.append({
            "deployment_name": r.deployment_name,
            "grade": score["grade"],
            "pass_pct": score["pass_pct"],
            "critical_fail": score["critical_fail"],
        })
    return {"project_name": project_name, "deployments": rows}
