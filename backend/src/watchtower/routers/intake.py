"""Project intake - one-shot: create Foundry project + model deployment + APIM publish + KV secret.
Captures full ownership + governance metadata; injects it into APIM headers, metric dimensions, and KV tags
so downstream logging + chargeback attribution works from the moment the endpoint goes live.
"""
import re
import traceback
from importlib.resources import files
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from azure.core.exceptions import HttpResponseError
from ..azure_clients import FoundryClient, ApimClient, KeyVaultClient, AuthClient
from ..config import get_settings
from ..db import get_db
from ..models import DeploymentRecord, AuditLog

router = APIRouter(prefix="/api/projects", tags=["intake"])


def _stage_error(stage: str, e: Exception) -> HTTPException:
    """Format any exception (Azure or otherwise) as an actionable HTTP 500 with a stage tag.
    Unpacks Azure's nested error.details[] so field-level messages surface (not just the generic top-level)."""
    if isinstance(e, HttpResponseError):
        top = getattr(e, "error", None)
        code = (top and top.code) or e.status_code
        msg = (top and top.message) or str(e)
        detail_msgs: list[str] = []
        # Azure often nests specific field errors here
        for d in (getattr(top, "details", None) or []):
            d_code = getattr(d, "code", None) or (d.get("code") if isinstance(d, dict) else None)
            d_msg = getattr(d, "message", None) or (d.get("message") if isinstance(d, dict) else None)
            d_target = getattr(d, "target", None) or (d.get("target") if isinstance(d, dict) else None)
            piece = f"{d_code}: {d_msg}" + (f" (target={d_target})" if d_target else "")
            detail_msgs.append(piece)
        combined = msg + (" | " + " | ".join(detail_msgs) if detail_msgs else "")
        return HTTPException(500, f"[{stage}] Azure {code}: {combined}")
    return HTTPException(500, f"[{stage}] {type(e).__name__}: {e}")

_POLICY_XML = None


def _load_policy_template() -> str:
    global _POLICY_XML
    if _POLICY_XML is None:
        _POLICY_XML = (files("watchtower.policies") / "main_policy.xml").read_text(encoding="utf-8")
    return _POLICY_XML


def _safe_id(name: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", name.lower())[:64]


class ProjectIntake(BaseModel):
    # Project
    project_name: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9-]*$", max_length=63)
    create_project: bool = True

    # Deployment
    deployment_name: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9-]+$")
    model_name: str
    model_version: str
    model_format: str = "OpenAI"
    sku_name: str = "GlobalStandard"
    capacity: int = 1
    rai_policy_name: str = "watchtower-balanced"

    # Use case
    use_case_description: str = Field(min_length=10, max_length=2000)

    # Ownership (required)
    app_name: str = Field(min_length=1, max_length=120)
    app_owner: str = Field(min_length=1, max_length=200)
    app_team: str = Field(min_length=1, max_length=120)
    business_unit: str = Field(min_length=1, max_length=120)
    environment: str = Field(pattern=r"^(dev|test|staging|prod)$")
    cost_center: str | None = None

    # Governance
    tpm_limit: int = Field(default=10000, ge=100, le=10_000_000)
    throttling_rpm: int = Field(default=60, ge=1, le=100_000)
    monthly_budget_usd: float = Field(default=500, ge=0)
    threshold_pct: int = Field(default=95, ge=50, le=100)

    tags: dict[str, str] | None = None


@router.post("/intake", status_code=201)
async def intake(payload: ProjectIntake, db: AsyncSession = Depends(get_db)):
    """One-shot: creates the project (if requested), the model deployment, publishes through APIM with
    all metadata + governance policies applied, and stores the consumer key in Key Vault."""
    s = get_settings()
    foundry = FoundryClient()
    apim = ApimClient()
    kv = KeyVaultClient()
    auth = AuthClient()

    stages: list[dict] = []

    def note(step: str, ok: bool, detail: str = ""):
        stages.append({"step": step, "ok": ok, "detail": detail})

    # 1. Ensure project (requires Foundry.allowProjectManagement=true + account SystemAssigned MI)
    if payload.create_project and payload.project_name:
        try:
            foundry.create_project(
                payload.project_name,
                display_name=f"{payload.app_name} ({payload.environment})",
                description=payload.use_case_description[:500],
            )
            note("project.create", True, f"Created project '{payload.project_name}'")
        except Exception as e:
            # PUT is idempotent on some paths but ARM sometimes returns 409/400 for existing.
            # Surface the actual reason but keep going - the deployment doesn't require the project wrapper.
            note("project.create", False, f"Skipped: {e}")

    # 2. Create the Foundry deployment (RAI auto-created if missing)
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
        note("deployment.create", True, f"Model deployment '{payload.deployment_name}' provisioned")
    except Exception as e:
        raise _stage_error("foundry.deployment.create", e)

    # 3. APIM MI + assisted RBAC to Foundry
    try:
        svc = apim.get_service()
    except Exception as e:
        raise _stage_error("apim.get_service", e)
    if not svc.get("identity_principal_id"):
        raise HTTPException(400, "APIM has no managed identity enabled - enable SystemAssigned first.")
    try:
        auth.ensure_apim_can_call_foundry(svc["identity_principal_id"])
        note("apim.rbac", True, "APIM MI granted Cognitive Services OpenAI User on Foundry")
    except Exception as e:
        raise _stage_error("apim.rbac.grant", e)

    # 4. Backend + API + operations
    backend_id = _safe_id(f"be-{s.foundry_account}")
    try:
        foundry_endpoint = foundry.get_endpoint()
        apim.get_or_create_backend(backend_id, foundry_endpoint)
    except Exception as e:
        raise _stage_error("apim.backend.upsert", e)

    api_id = _safe_id(f"ai-{payload.deployment_name}")
    api_path = f"ai/{_safe_id(payload.deployment_name)}"
    try:
        apim.create_api(api_id, api_path, display_name=f"AI Watchtower - {payload.app_name} - {payload.deployment_name}")
    except Exception as e:
        raise _stage_error("apim.api.create", e)

    try:
        for op_id, method, path, name in [
            ("chat-completions", "POST", f"/deployments/{payload.deployment_name}/chat/completions", "Chat Completions"),
            ("completions",      "POST", f"/deployments/{payload.deployment_name}/completions",      "Completions"),
            ("embeddings",       "POST", f"/deployments/{payload.deployment_name}/embeddings",       "Embeddings"),
        ]:
            apim.create_operation(api_id, op_id, method, path, name)
    except Exception as e:
        raise _stage_error("apim.operations.create", e)

    # APIM Named Value cannot be empty - seed with a placeholder no deployment name can match.
    try:
        if apim.get_named_value("watchtower-suspended-deployments") is None:
            apim.set_named_value("watchtower-suspended-deployments", "__none__")
    except Exception as e:
        raise _stage_error("apim.named-value.suspend-check", e)

    # 5. Tag the APIM API with all ownership metadata (inventory identification)
    for tag_key, tag_val in [
        ("app-name", payload.app_name),
        ("app-owner", payload.app_owner),
        ("app-team", payload.app_team),
        ("business-unit", payload.business_unit),
        ("environment", payload.environment),
        ("cost-center", payload.cost_center or ""),
    ]:
        if tag_val:
            try:
                apim.ensure_tag_and_assign_to_api(tag_key, tag_val, api_id)
            except Exception:
                pass

    # 6. Policy with all metadata + TPM + RPM substituted.
    # APIM rejects empty <value></value> in set-header, so replace empty strings with "none"
    # (still shows up in gateway logs, just indicates unset).
    def _val(v):
        v = (v or "").strip()
        return v if v else "none"

    policy = (
        _load_policy_template()
        .replace("{DEPLOYMENT_NAME}", payload.deployment_name)
        .replace("{MODEL_NAME}", payload.model_name)
        .replace("{BACKEND_ID}", backend_id)
        .replace("{FOUNDRY_ENDPOINT}", foundry_endpoint)
        .replace("{TPM_LIMIT}", str(payload.tpm_limit))
        .replace("{RPM_LIMIT}", str(payload.throttling_rpm))
        .replace("{APP_NAME}", _val(payload.app_name))
        .replace("{APP_OWNER}", _val(payload.app_owner))
        .replace("{APP_TEAM}", _val(payload.app_team))
        .replace("{BUSINESS_UNIT}", _val(payload.business_unit))
        .replace("{ENVIRONMENT}", _val(payload.environment))
        .replace("{COST_CENTER}", _val(payload.cost_center))
    )
    try:
        apim.set_api_policy(api_id, policy)
        note("apim.policy", True, "Governance policy applied (metadata headers, TPM+RPM, token metric, MSI backend)")
    except Exception as e:
        raise _stage_error("apim.policy.apply", e)

    # 7. Product + subscription
    try:
        product = apim.get_or_create_product()
        apim.add_api_to_product(product["name"], api_id)
    except Exception as e:
        raise _stage_error("apim.product.upsert", e)

    sub_id = _safe_id(f"sub-{payload.deployment_name}")
    try:
        sub = apim.create_subscription(sub_id, api_id, display_name=f"AI Watchtower - {payload.deployment_name}")
        note("apim.subscription", True, f"APIM subscription '{sub_id}' created")
    except Exception as e:
        raise _stage_error("apim.subscription.create", e)

    # 8. KV secret with full metadata tags
    kv_tags = {
        "deployment": payload.deployment_name,
        "app-name": payload.app_name,
        "app-owner": payload.app_owner,
        "app-team": payload.app_team,
        "business-unit": payload.business_unit,
        "environment": payload.environment,
        "cost-center": payload.cost_center or "",
        "source": "ai-watchtower",
    }
    try:
        kv_result = kv.set_secret(
            name=f"apim-key-{_safe_id(payload.deployment_name)}",
            value=sub["primary_key"],
            tags={k: v for k, v in kv_tags.items() if v},
        )
        note("kv.secret", True, f"Consumer key stored at {kv_result['uri']}")
    except Exception as e:
        raise _stage_error("keyvault.secret.set", e)

    # 9. Persist Watchtower record
    record = DeploymentRecord(
        deployment_name=payload.deployment_name,
        foundry_account=s.foundry_account,
        project_name=payload.project_name,
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
        apim_api_id=api_id,
        apim_subscription_id=sub_id,
        kv_secret_name=kv_result["name"],
        kv_secret_uri=kv_result["uri"],
        state="active",
    )
    db.add(record)
    db.add(AuditLog(
        actor="watchtower-ui",
        action="project.intake",
        target_type="deployment",
        target_id=payload.deployment_name,
        after={
            "app_name": payload.app_name,
            "app_team": payload.app_team,
            "business_unit": payload.business_unit,
            "environment": payload.environment,
            "tpm_limit": payload.tpm_limit,
            "throttling_rpm": payload.throttling_rpm,
            "monthly_budget_usd": payload.monthly_budget_usd,
        },
    ))
    await db.commit()

    # 10. Return the useful bits for the UI success screen
    endpoint_url = f"{svc['gateway_url']}/{api_path}"
    return {
        "deployment": created,
        "project_name": payload.project_name,
        "endpoint_url": endpoint_url,
        "apim_api_id": api_id,
        "apim_subscription_id": sub_id,
        "apim_subscription_key_kv_uri": kv_result["uri"],
        "stages": stages,
        "monitoring": {
            "app_insights_namespace": "ai-watchtower",
            "metric_dimensions": ["DeploymentName", "ConsumerId", "ModelName", "AppName", "AppTeam", "BusinessUnit", "Environment", "CostCenter"],
        },
    }
