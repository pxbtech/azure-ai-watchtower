import re
from importlib.resources import files
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..azure_clients import FoundryClient, ApimClient, KeyVaultClient, AuthClient
from ..config import get_settings
from ..db import get_db
from ..models import DeploymentRecord, SuspensionRecord, AuditLog

router = APIRouter(prefix="/api/deployments", tags=["deployments"])

_POLICY_XML = None


def _load_policy_template() -> str:
    global _POLICY_XML
    if _POLICY_XML is None:
        _POLICY_XML = (files("watchtower.policies") / "main_policy.xml").read_text(encoding="utf-8")
    return _POLICY_XML


def _safe_id(name: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", name.lower())[:64]


def _apply_policy(apim: ApimClient, foundry: FoundryClient, record: DeploymentRecord) -> None:
    """Re-render and push the APIM policy for a Watchtower record.
    APIM rejects empty <value></value>, so empty metadata gets 'none' sentinel."""
    def _v(x):
        v = (x or "").strip()
        return v if v else "none"

    backend_id = _safe_id(f"be-{record.foundry_account}")
    policy = (
        _load_policy_template()
        .replace("{DEPLOYMENT_NAME}", record.deployment_name)
        .replace("{MODEL_NAME}", record.model_name)
        .replace("{BACKEND_ID}", backend_id)
        .replace("{FOUNDRY_ENDPOINT}", foundry.get_endpoint())
        .replace("{TPM_LIMIT}", str(record.tpm_limit))
        .replace("{RPM_LIMIT}", str(record.throttling_rpm))
        .replace("{APP_NAME}", _v(record.app_name))
        .replace("{APP_OWNER}", _v(record.app_owner))
        .replace("{APP_TEAM}", _v(record.app_team))
        .replace("{BUSINESS_UNIT}", _v(record.business_unit))
        .replace("{ENVIRONMENT}", _v(record.environment))
        .replace("{COST_CENTER}", _v(record.cost_center))
    )
    apim.set_api_policy(record.apim_api_id, policy)


class DeploymentCreate(BaseModel):
    deployment_name: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9-]+$")
    model_name: str
    model_version: str
    model_format: str = "OpenAI"
    sku_name: str = "GlobalStandard"
    capacity: int = 1
    rai_policy_name: str = "watchtower-balanced"
    publish_to_apim: bool = True

    app_name: str = Field(min_length=1, max_length=120)
    app_owner: str | None = None
    app_team: str | None = None
    business_unit: str | None = None
    environment: str = "prod"
    cost_center: str | None = None
    use_case_description: str | None = None
    tags: dict[str, str] | None = None

    tpm_limit: int = 10000
    throttling_rpm: int = 60
    monthly_budget_usd: float | None = None
    threshold_pct: int = 95


@router.get("/foundry")
async def list_foundry_deployments():
    try:
        return FoundryClient().list_deployments()
    except Exception as e:
        raise HTTPException(500, f"Foundry deployment list failed: {e}")


@router.get("/models/available")
async def list_available_models():
    try:
        return FoundryClient().list_available_models()
    except Exception as e:
        raise HTTPException(500, f"Model list failed: {e}")


@router.post("", status_code=201)
async def create_deployment(payload: DeploymentCreate, db: AsyncSession = Depends(get_db)):
    foundry = FoundryClient()
    apim = ApimClient()
    kv = KeyVaultClient()
    auth = AuthClient()
    s = get_settings()

    try:
        created = foundry.create_deployment(
            deployment_name=payload.deployment_name,
            model_name=payload.model_name,
            model_version=payload.model_version,
            model_format=payload.model_format,
            sku_name=payload.sku_name,
            capacity=payload.capacity,
            rai_policy_name=payload.rai_policy_name,
        )
    except Exception as e:
        raise HTTPException(500, f"Foundry deployment create failed: {e}")

    record = DeploymentRecord(
        deployment_name=payload.deployment_name,
        foundry_account=s.foundry_account,
        model_name=payload.model_name,
        model_version=payload.model_version,
        sku_name=payload.sku_name,
        capacity=payload.capacity,
        rai_policy_name=payload.rai_policy_name,
        app_name=payload.app_name,
        app_owner=payload.app_owner,
        app_team=payload.app_team,
        business_unit=payload.business_unit,
        environment=payload.environment,
        cost_center=payload.cost_center,
        use_case_description=payload.use_case_description,
        tags=payload.tags or {},
        tpm_limit=payload.tpm_limit,
        throttling_rpm=payload.throttling_rpm,
        monthly_budget_usd=payload.monthly_budget_usd,
        threshold_pct=payload.threshold_pct,
        state="active",
    )

    if payload.publish_to_apim:
        svc = apim.get_service()
        if not svc.get("identity_principal_id"):
            raise HTTPException(400, "APIM has no managed identity. Enable one first.")
        auth.ensure_apim_can_call_foundry(svc["identity_principal_id"])

        backend_id = _safe_id(f"be-{s.foundry_account}")
        foundry_endpoint = foundry.get_endpoint()
        apim.get_or_create_backend(backend_id, foundry_endpoint)

        api_id = _safe_id(f"ai-{payload.deployment_name}")
        api_path = f"ai/{_safe_id(payload.deployment_name)}"
        apim.create_api(api_id, api_path, display_name=f"AI Watchtower - {payload.deployment_name}")

        for op_id, method, path, name in [
            ("chat-completions", "POST", f"/deployments/{payload.deployment_name}/chat/completions", "Chat Completions"),
            ("completions", "POST", f"/deployments/{payload.deployment_name}/completions", "Completions"),
            ("embeddings", "POST", f"/deployments/{payload.deployment_name}/embeddings", "Embeddings"),
        ]:
            apim.create_operation(api_id, op_id, method, path, name)

        if apim.get_named_value("watchtower-suspended-deployments") is None:
            apim.set_named_value("watchtower-suspended-deployments", "__none__")

        for tag_key, tag_val in [
            ("app-name", payload.app_name),
            ("app-team", payload.app_team or ""),
            ("business-unit", payload.business_unit or ""),
            ("environment", payload.environment or ""),
            ("cost-center", payload.cost_center or ""),
        ]:
            if tag_val:
                try:
                    apim.ensure_tag_and_assign_to_api(tag_key, tag_val, api_id)
                except Exception:
                    pass

        record.apim_api_id = api_id
        _apply_policy(apim, foundry, record)

        product = apim.get_or_create_product()
        apim.add_api_to_product(product["name"], api_id)
        sub_id = _safe_id(f"sub-{payload.deployment_name}")
        sub = apim.create_subscription(sub_id, api_id, display_name=f"AI Watchtower - {payload.deployment_name}")

        kv_result = kv.set_secret(
            name=f"apim-key-{_safe_id(payload.deployment_name)}",
            value=sub["primary_key"],
            tags={
                "deployment": payload.deployment_name,
                "app-name": payload.app_name,
                "app-team": payload.app_team or "",
                "source": "ai-watchtower",
            },
        )
        record.apim_subscription_id = sub_id
        record.kv_secret_name = kv_result["name"]
        record.kv_secret_uri = kv_result["uri"]

    db.add(record)
    db.add(AuditLog(
        actor="watchtower-api",
        action="deployment.create",
        target_type="deployment",
        target_id=payload.deployment_name,
        after={
            "deployment_name": payload.deployment_name,
            "model": payload.model_name,
            "app_name": payload.app_name,
            "tpm_limit": payload.tpm_limit,
            "throttling_rpm": payload.throttling_rpm,
            "monthly_budget_usd": payload.monthly_budget_usd,
        },
    ))
    await db.commit()

    return {
        "deployment": created,
        "apim_api_id": record.apim_api_id,
        "apim_subscription_id": record.apim_subscription_id,
        "kv_secret_uri": record.kv_secret_uri,
        "state": record.state,
    }


def _record_to_dict(r: DeploymentRecord) -> dict:
    return {
        "deployment_name": r.deployment_name,
        "foundry_account": r.foundry_account,
        "project_name": r.project_name,
        "model_name": r.model_name,
        "model_version": r.model_version,
        "sku_name": r.sku_name,
        "capacity": r.capacity,
        "rai_policy_name": r.rai_policy_name,
        "app_name": r.app_name,
        "app_owner": r.app_owner,
        "app_team": r.app_team,
        "business_unit": r.business_unit,
        "environment": r.environment,
        "cost_center": r.cost_center,
        "use_case_description": r.use_case_description,
        "tags": r.tags or {},
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


@router.get("/records")
async def list_records(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DeploymentRecord).order_by(DeploymentRecord.created_at.desc()))
    return [_record_to_dict(r) for r in result.scalars().all()]


class SuspendPayload(BaseModel):
    reason: str | None = None
    actor: str = "watchtower-ui"


@router.post("/{deployment_name}/suspend")
async def suspend(deployment_name: str, payload: SuspendPayload, db: AsyncSession = Depends(get_db)):
    apim = ApimClient()
    layers = {"named_value": False, "subscription": False}

    try:
        apim.add_to_suspended_csv(deployment_name)
        layers["named_value"] = True
    except Exception as e:
        raise HTTPException(500, f"Layer 1 (named value) failed: {e}")

    result = await db.execute(select(DeploymentRecord).where(DeploymentRecord.deployment_name == deployment_name))
    record = result.scalar_one_or_none()
    if record and record.apim_subscription_id:
        try:
            apim.set_subscription_state(record.apim_subscription_id, "suspended")
            layers["subscription"] = True
        except Exception as e:
            layers["subscription_error"] = str(e)

    if record:
        record.state = "suspended"

    db.add(SuspensionRecord(deployment_name=deployment_name, action="suspend", layers_applied=layers, actor=payload.actor, reason=payload.reason))
    db.add(AuditLog(actor=payload.actor, action="deployment.suspend", target_type="deployment", target_id=deployment_name, after={"layers": layers, "reason": payload.reason}))
    await db.commit()
    return {"deployment_name": deployment_name, "state": "suspended", "layers_applied": layers}


@router.post("/{deployment_name}/unsuspend")
async def unsuspend(deployment_name: str, payload: SuspendPayload, db: AsyncSession = Depends(get_db)):
    apim = ApimClient()
    layers = {"named_value": False, "subscription": False}

    result = await db.execute(select(DeploymentRecord).where(DeploymentRecord.deployment_name == deployment_name))
    record = result.scalar_one_or_none()
    if record and record.apim_subscription_id:
        try:
            apim.set_subscription_state(record.apim_subscription_id, "active")
            layers["subscription"] = True
        except Exception as e:
            layers["subscription_error"] = str(e)

    try:
        apim.remove_from_suspended_csv(deployment_name)
        layers["named_value"] = True
    except Exception as e:
        raise HTTPException(500, f"Layer 1 (named value) removal failed: {e}")

    if record:
        record.state = "active"

    db.add(SuspensionRecord(deployment_name=deployment_name, action="unsuspend", layers_applied=layers, actor=payload.actor, reason=payload.reason))
    db.add(AuditLog(actor=payload.actor, action="deployment.unsuspend", target_type="deployment", target_id=deployment_name, after={"layers": layers, "reason": payload.reason}))
    await db.commit()
    return {"deployment_name": deployment_name, "state": "active", "layers_applied": layers}


class UpdatePayload(BaseModel):
    tpm_limit: int | None = None
    throttling_rpm: int | None = None
    monthly_budget_usd: float | None = None
    threshold_pct: int | None = None
    app_owner: str | None = None
    app_team: str | None = None
    business_unit: str | None = None
    environment: str | None = None
    cost_center: str | None = None


def _has_change(new_val, current_val) -> bool:
    """A str update counts as change only if it's a real, different value.
    Empty strings from the UI mean 'not touching this field'."""
    if new_val is None:
        return False
    if isinstance(new_val, str) and new_val == (current_val or ""):
        return False
    if not isinstance(new_val, str) and new_val == current_val:
        return False
    return True


@router.delete("/{deployment_name}")
async def delete_deployment(deployment_name: str, db: AsyncSession = Depends(get_db)):
    """Full cascade delete: APIM API + subscription, KV secret (+ purge from soft-delete),
    Foundry deployment, Watchtower DB rows. Idempotent - safe to call on partially-cleaned records."""
    from ..azure_clients import ApimClient, KeyVaultClient, FoundryClient
    result = await db.execute(select(DeploymentRecord).where(DeploymentRecord.deployment_name == deployment_name))
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(404, "Deployment not tracked by Watchtower.")

    steps: list[dict] = []
    apim = ApimClient()
    kv = KeyVaultClient()
    foundry = FoundryClient()

    # 1. APIM subscription (unblocks API delete cleanly)
    if record.apim_subscription_id:
        try:
            apim.delete_subscription(record.apim_subscription_id)
            steps.append({"step": "apim.subscription.delete", "ok": True})
        except Exception as e:
            steps.append({"step": "apim.subscription.delete", "ok": False, "error": str(e)})
    # 2. APIM API (operations + policy + product bindings cascade)
    if record.apim_api_id:
        try:
            apim.delete_api(record.apim_api_id)
            steps.append({"step": "apim.api.delete", "ok": True})
        except Exception as e:
            steps.append({"step": "apim.api.delete", "ok": False, "error": str(e)})
    # 3. KV secret + purge from soft-delete recovery bin
    if record.kv_secret_name:
        try:
            r = kv.delete_and_purge_secret(record.kv_secret_name)
            steps.append({"step": "kv.secret.delete", "ok": r["deleted"], "purged": r["purged"]})
        except Exception as e:
            steps.append({"step": "kv.secret.delete", "ok": False, "error": str(e)})
    # 4. Foundry deployment
    try:
        ok = foundry.delete_deployment(record.deployment_name)
        steps.append({"step": "foundry.deployment.delete", "ok": ok})
    except Exception as e:
        steps.append({"step": "foundry.deployment.delete", "ok": False, "error": str(e)})
    # 5. Also try to unsuspend from CSV (cleanup lingering suspend state)
    try:
        apim.remove_from_suspended_csv(record.deployment_name)
    except Exception:
        pass
    # 6. Watchtower DB rows (deployment_records, suspension_records, audit_log entries for this name)
    try:
        from sqlalchemy import delete as sql_delete
        from ..models import SuspensionRecord, AuditLog
        await db.execute(sql_delete(SuspensionRecord).where(SuspensionRecord.deployment_name == deployment_name))
        await db.execute(sql_delete(AuditLog).where(AuditLog.target_id == deployment_name))
        await db.execute(sql_delete(DeploymentRecord).where(DeploymentRecord.deployment_name == deployment_name))
        # Write a final audit entry documenting the deletion
        db.add(AuditLog(
            actor="watchtower-ui",
            action="deployment.delete",
            target_type="deployment",
            target_id=deployment_name,
            after={"steps": steps},
        ))
        await db.commit()
        steps.append({"step": "watchtower.db.delete", "ok": True})
    except Exception as e:
        steps.append({"step": "watchtower.db.delete", "ok": False, "error": str(e)})

    return {"deployment_name": deployment_name, "steps": steps}


@router.get("/{deployment_name}/config-drift")
async def deployment_config_drift(deployment_name: str, hours: int = 168):
    """Configuration changes affecting this specific deployment (its own writes + account-level).
    Real Azure Activity Log events, no mockup."""
    from ..azure_clients import MonitorClient
    m = MonitorClient()
    return {
        "deployment_name": deployment_name,
        "hours": hours,
        "rows": m.query_config_drift(hours=hours, deployment_name=deployment_name),
    }


@router.post("/{deployment_name}/budget-check")
async def budget_check(deployment_name: str, db: AsyncSession = Depends(get_db)):
    """Manually run the budget enforcement check for this deployment. Same logic as the
    background worker but scoped to one deployment. Returns status + action taken."""
    from ..workers.budget_enforcement import compute_status_for, _auto_suspend
    from ..azure_clients import MonitorClient, ApimClient
    result = await db.execute(select(DeploymentRecord).where(DeploymentRecord.deployment_name == deployment_name))
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(404, "Deployment not tracked by Watchtower.")

    monitor = MonitorClient()
    diag = monitor.check_apim_diagnostics()
    workspace_id = diag.get("workspace_id")
    status = await compute_status_for(record, monitor, workspace_id)
    action = None
    if record.state == "active" and status.get("over_threshold") and status.get("mtd_cost_usd") is not None:
        ok, reason = await _auto_suspend(record, status["mtd_cost_usd"], ApimClient())
        action = {"suspended": ok, "reason": reason}
    return {"status": status, "action": action, "state": record.state}


@router.post("/budget-check-all")
async def budget_check_all():
    """Trigger a full sweep on demand (same as the 5-min background pass)."""
    from ..workers.budget_enforcement import run_once
    return await run_once()


@router.patch("/{deployment_name}")
async def update_deployment(deployment_name: str, payload: UpdatePayload, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DeploymentRecord).where(DeploymentRecord.deployment_name == deployment_name))
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(404, "Deployment not tracked by Watchtower.")

    before = _record_to_dict(record)
    changed_policy_fields = False

    if _has_change(payload.tpm_limit, record.tpm_limit):
        record.tpm_limit = payload.tpm_limit; changed_policy_fields = True
    if _has_change(payload.throttling_rpm, record.throttling_rpm):
        record.throttling_rpm = payload.throttling_rpm; changed_policy_fields = True
    if _has_change(payload.monthly_budget_usd, record.monthly_budget_usd):
        record.monthly_budget_usd = payload.monthly_budget_usd
    if _has_change(payload.threshold_pct, record.threshold_pct):
        record.threshold_pct = payload.threshold_pct

    # For str metadata, empty string means "leave it alone" (avoids accidental clearing).
    if payload.app_owner and _has_change(payload.app_owner, record.app_owner):
        record.app_owner = payload.app_owner; changed_policy_fields = True
    if payload.app_team and _has_change(payload.app_team, record.app_team):
        record.app_team = payload.app_team; changed_policy_fields = True
    if payload.business_unit and _has_change(payload.business_unit, record.business_unit):
        record.business_unit = payload.business_unit; changed_policy_fields = True
    if payload.environment and _has_change(payload.environment, record.environment):
        record.environment = payload.environment; changed_policy_fields = True
    if payload.cost_center and _has_change(payload.cost_center, record.cost_center):
        record.cost_center = payload.cost_center; changed_policy_fields = True

    policy_error: str | None = None
    if changed_policy_fields and record.apim_api_id:
        try:
            _apply_policy(ApimClient(), FoundryClient(), record)
        except Exception as e:
            # Don't lose the DB update if APIM policy rewrite fails.
            # Surface the error so the UI can flag it.
            top = getattr(e, "error", None)
            code = (top and getattr(top, "code", None)) or type(e).__name__
            msg = (top and getattr(top, "message", None)) or str(e)
            policy_error = f"[{code}] {msg}"

    db.add(AuditLog(
        actor="watchtower-ui",
        action="deployment.update",
        target_type="deployment",
        target_id=deployment_name,
        before=before,
        after=_record_to_dict(record),
    ))
    await db.commit()

    result = _record_to_dict(record)
    if policy_error:
        result["_policy_error"] = policy_error
    return result
