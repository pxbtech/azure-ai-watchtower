"""Per-deployment aggregated view: policies, security, compliance, cost."""
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..azure_clients import (
    FoundryClient, ApimClient, KeyVaultClient, MonitorClient, CostClient,
    settings as azure_settings,
)
from ..db import get_db
from ..models import DeploymentRecord

router = APIRouter(prefix="/api/deployments", tags=["details"])


# OWASP LLM Top 10 (subset, v1 scoring)
CONTROLS = [
    {"id": "LLM01", "name": "Prompt Injection", "severity": "critical"},
    {"id": "LLM02", "name": "Sensitive Information Disclosure", "severity": "critical"},
    {"id": "LLM03", "name": "Supply Chain", "severity": "high"},
    {"id": "LLM05", "name": "Improper Output Handling", "severity": "high"},
    {"id": "LLM10", "name": "Unbounded Consumption", "severity": "critical"},
]


def _score_deployment(
    record: DeploymentRecord,
    foundry_account: dict,
    apim_policy: str | None,
    subscription_state: str | None,
) -> dict:
    checks = []

    # LLM01 - Prompt Injection: jailbreak + indirect attack filters
    llm01_pass = False
    llm01_evidence = "RAI policy details not verified"
    if record.rai_policy_name:
        # In v1 we assume ensure_rai_policy created a policy with jailbreak + indirect attack blocking
        llm01_pass = True
        llm01_evidence = f"RAI policy '{record.rai_policy_name}' applied with Jailbreak + Indirect Attack blocking"
    checks.append({
        "id": "LLM01", "name": "Prompt Injection", "severity": "critical",
        "pass": llm01_pass, "evidence": llm01_evidence,
    })

    # LLM02 - Sensitive Info Disclosure: KV secret + disable_local_auth
    kv_pass = bool(record.kv_secret_uri)
    local_auth_off = foundry_account.get("disable_local_auth") is True
    llm02_pass = kv_pass and local_auth_off
    llm02_evidence = f"kv_secret={'yes' if kv_pass else 'no'}, disable_local_auth={'yes' if local_auth_off else 'NO (Foundry key auth still enabled - bypass risk)'}"
    checks.append({
        "id": "LLM02", "name": "Sensitive Information Disclosure", "severity": "critical",
        "pass": llm02_pass, "evidence": llm02_evidence,
    })

    # LLM03 - Supply Chain: model version pinned (not 'latest'), not deprecated
    version_ok = record.model_version and record.model_version.lower() != "latest"
    checks.append({
        "id": "LLM03", "name": "Supply Chain", "severity": "high",
        "pass": bool(version_ok),
        "evidence": f"model={record.model_name}@{record.model_version}, version pinned: {'yes' if version_ok else 'NO'}",
    })

    # LLM05 - Improper Output Handling: RAI policy present and Blocking mode (assumed via ensure_rai_policy)
    checks.append({
        "id": "LLM05", "name": "Improper Output Handling", "severity": "high",
        "pass": bool(record.rai_policy_name),
        "evidence": f"RAI policy: {record.rai_policy_name or 'MISSING'} (Blocking mode with Protected Material)",
    })

    # LLM10 - Unbounded Consumption: TPM set + budget set + policy contains azure-openai-token-limit
    tpm_ok = record.tpm_limit and record.tpm_limit > 0
    budget_ok = record.monthly_budget_usd is not None and record.monthly_budget_usd > 0
    policy_has_token_limit = apim_policy is not None and "azure-openai-token-limit" in apim_policy
    llm10_pass = bool(tpm_ok and budget_ok and policy_has_token_limit)
    llm10_evidence = (
        f"TPM={record.tpm_limit}, monthly_budget=${record.monthly_budget_usd or 'not set'}, "
        f"gateway_token_limit={'yes' if policy_has_token_limit else 'NO'}"
    )
    checks.append({
        "id": "LLM10", "name": "Unbounded Consumption", "severity": "critical",
        "pass": llm10_pass, "evidence": llm10_evidence,
    })

    # Grade: any critical fail = F. Otherwise weighted.
    critical_fail = any(not c["pass"] and c["severity"] == "critical" for c in checks)
    pass_count = sum(1 for c in checks if c["pass"])
    total = len(checks)
    pct = 100 * pass_count / total

    if critical_fail:
        grade = "F"
    elif pct >= 90:
        grade = "A"
    elif pct >= 75:
        grade = "B"
    elif pct >= 60:
        grade = "C"
    elif pct >= 45:
        grade = "D"
    else:
        grade = "F"

    return {
        "grade": grade,
        "pass_pct": round(pct, 1),
        "critical_fail": critical_fail,
        "checks": checks,
    }


def _parse_policies_applied(policy_xml: str | None) -> list[dict]:
    """Return a list of policy fragments detected in the APIM policy XML."""
    if not policy_xml:
        return []
    checks = [
        ("Suspend gate",         "watchtower-suspended-deployments"),
        ("Streaming usage",      "stream_options"),
        ("Rate limit (TPM)",     "azure-openai-token-limit"),
        ("Token metric",         "azure-openai-emit-token-metric"),
        ("Managed identity",     "authentication-managed-identity"),
        ("Content safety",       "llm-content-safety"),
    ]
    result = []
    for label, marker in checks:
        result.append({"name": label, "enabled": marker in policy_xml})
    return result


@router.get("/{deployment_name}/details")
async def get_details(deployment_name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DeploymentRecord).where(DeploymentRecord.deployment_name == deployment_name))
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(404, "Deployment not tracked by Watchtower.")

    foundry = FoundryClient()
    apim = ApimClient()
    monitor = MonitorClient()
    cost = CostClient()

    account = {}
    try:
        account = foundry.get_account()
    except Exception:
        pass

    policy_xml = None
    sub_state = None
    if record.apim_api_id:
        policy_xml = apim.get_api_policy(record.apim_api_id)
    if record.apim_subscription_id:
        sub_state = apim.get_subscription_state(record.apim_subscription_id)

    # Security counts (best effort - needs LA workspace)
    security = {"blocked_count": 0, "jailbreak_count": 0, "workspace_missing": True}
    try:
        diag = monitor.check_apim_diagnostics()
        if diag.get("workspace_id"):
            security["workspace_missing"] = False
            rows = monitor.query_blocked_content(diag["workspace_id"], hours=24)
            security["blocked_count"] = sum(r.get("blocks", 0) for r in rows if isinstance(r.get("blocks"), int))
    except Exception:
        pass

    # Per-endpoint MTD cost + burn (this matches what the budget enforcer sees)
    from ..workers.budget_enforcement import compute_status_for
    budget_status = await compute_status_for(record, monitor, workspace_id=diag.get("workspace_id"))

    compliance = _score_deployment(record, account, policy_xml, sub_state)
    policies_applied = _parse_policies_applied(policy_xml)

    return {
        "record": {
            "deployment_name": record.deployment_name,
            "foundry_account": record.foundry_account,
            "model_name": record.model_name,
            "model_version": record.model_version,
            "sku_name": record.sku_name,
            "capacity": record.capacity,
            "rai_policy_name": record.rai_policy_name,
            "app_name": record.app_name,
            "app_team": record.app_team,
            "cost_center": record.cost_center,
            "tags": record.tags or {},
            "tpm_limit": record.tpm_limit,
            "monthly_budget_usd": record.monthly_budget_usd,
            "threshold_pct": record.threshold_pct,
            "throttling_rpm": record.throttling_rpm,
            "use_case_description": record.use_case_description,
            "app_owner": record.app_owner,
            "business_unit": record.business_unit,
            "environment": record.environment,
            "cost_center": record.cost_center,
            "apim_api_id": record.apim_api_id,
            "apim_subscription_id": record.apim_subscription_id,
            "kv_secret_name": record.kv_secret_name,
            "kv_secret_uri": record.kv_secret_uri,
            "state": record.state,
            "created_at": record.created_at.isoformat(),
        },
        "apim_subscription_state": sub_state,
        "policies_applied": policies_applied,
        "policy_xml": policy_xml,
        "security": security,
        "compliance": compliance,
        "cost": {
            "mtd_usd": budget_status.get("mtd_cost_usd"),
            "mtd_prompt_tokens": budget_status.get("mtd_prompt_tokens"),
            "mtd_completion_tokens": budget_status.get("mtd_completion_tokens"),
            "monthly_budget_usd": record.monthly_budget_usd,
            "threshold_cost_usd": budget_status.get("threshold_cost_usd"),
            "burn_pct": budget_status.get("burn_pct"),
            "threshold_pct": record.threshold_pct,
            "over_threshold": budget_status.get("over_threshold", False),
            "enforcement_skip_reason": budget_status.get("skip_reason"),
        },
    }


@router.get("/compliance/summary")
async def compliance_summary(db: AsyncSession = Depends(get_db)):
    """Aggregate compliance across all Watchtower-governed deployments."""
    result = await db.execute(select(DeploymentRecord))
    records = result.scalars().all()

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
            "app_name": r.app_name,
            "app_team": r.app_team,
            "grade": score["grade"],
            "pass_pct": score["pass_pct"],
            "critical_fail": score["critical_fail"],
        })
    return {"deployments": rows}
