# AI Watchtower - specification

This is the authoritative behavioral spec: what the platform does, what it enforces, and where it draws the line between "governed" and "operator's responsibility". It is not a design doc - see [`architecture.md`](./architecture.md) for the component picture and [`deployment.md`](./deployment.md) for install instructions.

## Purpose

Azure AI Foundry gives you model hosting. It does not give you:

- A standardised intake for new AI endpoints in an organisation
- Per-endpoint budget enforcement with auto-suspend
- OWASP LLM Top 10 compliance grading per model
- A single UI for consumption, cost, ownership metadata, and drift
- An audit trail of who provisioned what, when, with which policy

AI Watchtower fills that gap by putting API Management in front of every Foundry deployment it governs and orchestrating the full pipeline (Foundry project -> deployment -> APIM API -> APIM subscription -> Key Vault secret) from one form.

## Non-goals

- **Not a proxy**. AI Watchtower configures APIM to be the proxy; it doesn't sit in the request path itself.
- **Not a prompt firewall**. Content moderation is delegated to Azure Content Safety via the `llm-content-safety` APIM policy fragment. Watchtower ensures the fragment is applied but doesn't inspect prompts itself.
- **Not a chargeback / billing system**. Watchtower measures consumption per endpoint and computes cost estimates, but doesn't emit invoices, doesn't reconcile against Azure Cost Management, and doesn't handle currency conversion.
- **Not a model marketplace**. Watchtower publishes what's in your Foundry catalog. It doesn't compare models, benchmark, or recommend.

## Governed vs operator responsibility

**Governed by Watchtower:**

- APIM API + operations config for every published endpoint
- APIM policy XML (rate limits, MI auth, metadata injection)
- APIM subscription lifecycle (created at intake, suspended on budget breach, deleted on cascade)
- Key Vault secret for each endpoint's subscription key
- Foundry deployment metadata (RAI policy, capacity, SKU)
- Governance DB record (source of truth for "what state should this be in")
- Auto-suspend on budget threshold breach

**Operator's responsibility (Watchtower will not do these for you):**

- APIM v2 SKU (needed for token-limit policies)
- Log Analytics diagnostic settings on APIM (needed for cost + monitoring)
- Body-logging on APIM diagnostics (needed for token metric emission)
- Content Safety account provisioning (Watchtower will use it if you provide the name; won't create it)
- Foundry model quota (Watchtower can create deployments up to quota; won't request quota increases)
- Approval workflow before intake (Watchtower has no request/approval flow; wire your own via App Service Authentication + a preflight approval system if needed)
- Backup of the governance DB (Watchtower relies on Postgres Flex's built-in backups)

## Intake contract

Every intake submission carries:

- **Model selection**: model name, model version, SKU name, capacity. Populated live from Foundry catalog.
- **Ownership**: `app_name`, `app_owner`, `app_team`, `business_unit`, `environment`, `cost_center`. All required, all injected as custom HTTP headers (`X-App-Name`, `X-App-Owner`, ...) by the APIM policy on every downstream request. Empty strings are substituted with `"none"` sentinel by the intake orchestrator so APIM does not reject empty policy values.
- **Enforcement**: `tpm_limit` (token per minute), `throttling_rpm` (requests per minute), `monthly_budget_usd`, `threshold_pct` (0-100).
- **Use case**: free-text `use_case_description`. Not enforced, but required and surfaced in the deployment drawer for compliance reviews.
- **Project association**: `project_name`. New or existing. Used for grouping in the UI and for organisational rollups (budget sum across a project).

Intake returns:

```json
{
  "deployment_name": "sanitized-name",
  "apim_endpoint": "https://<gateway>/openai/deployments/<name>/chat/completions?api-version=...",
  "kv_secret_uri": "https://<kv>.vault.azure.net/secrets/<name>/<version>",
  "compliance_grade": "A",
  "stages_completed": ["foundry_project", "foundry_deployment", "apim_api", "apim_subscription", "kv_secret", "db_record"]
}
```

or on partial failure:

```json
{
  "error": "APIM policy validation failed",
  "stage": "apim_policy",
  "detail": "<raw ARM error>",
  "stages_completed": ["foundry_project", "foundry_deployment", "apim_api"]
}
```

The delete cascade will clean up anything created in `stages_completed`, so partial state is safe to leave in place while debugging.

## Deployment name sanitization

Deployment names must be `^[a-z0-9-]+$` (lowercase letters, digits, hyphens). The intake form applies this in the UI as the user types. If a user pastes something with spaces, uppercase, or underscores, they get replaced with hyphens and the result is lowercased. This matches both Foundry and APIM naming rules.

## APIM policy contract

Every AI Watchtower-published API has this policy applied (with placeholder substitution at intake time):

```xml
<policies>
  <inbound>
    <base />
    <!-- Metadata injection: on every request, tag the downstream Foundry call with ownership -->
    <set-header name="X-App-Name" exists-action="override"><value>{APP_NAME}</value></set-header>
    <set-header name="X-App-Owner" exists-action="override"><value>{APP_OWNER}</value></set-header>
    <set-header name="X-App-Team" exists-action="override"><value>{APP_TEAM}</value></set-header>
    <set-header name="X-Business-Unit" exists-action="override"><value>{BUSINESS_UNIT}</value></set-header>
    <set-header name="X-Environment" exists-action="override"><value>{ENVIRONMENT}</value></set-header>
    <set-header name="X-Cost-Center" exists-action="override"><value>{COST_CENTER}</value></set-header>

    <!-- Managed identity auth against Foundry (no keys in APIM) -->
    <authentication-managed-identity resource="https://cognitiveservices.azure.com" />

    <!-- Suspend gate: fires when this deployment is in the suspended-list named value -->
    <choose>
      <when condition="@{ return ((string)context.Deployment.Properties["watchtower-suspended-deployments"]).Split(',').Contains("{DEPLOYMENT_NAME}"); }">
        <return-response>
          <set-status code="429" reason="Suspended by AI Watchtower - budget exceeded" />
          <set-header name="Retry-After" exists-action="override"><value>3600</value></set-header>
        </return-response>
      </when>
    </choose>

    <!-- Rate limiting: RPM (per key) and TPM (token-aware, v2 only) -->
    <rate-limit-by-key calls="{RPM_LIMIT}" renewal-period="60" counter-key="@(context.Subscription.Id)" />
    <azure-openai-token-limit tokens-per-minute="{TPM_LIMIT}" counter-key="@(context.Subscription.Id)" estimate-prompt-tokens="true" />
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
    <!-- Token metric emission: gives Log Analytics the PromptTokens_d / CompletionTokens_d dimensions -->
    <azure-openai-emit-token-metric namespace="watchtower">
      <dimension name="Deployment" value="{DEPLOYMENT_NAME}" />
      <dimension name="AppName" value="{APP_NAME}" />
    </azure-openai-emit-token-metric>
  </outbound>
</policies>
```

When a user changes a governed field (TPM, RPM, budget, threshold, ownership), the deployment PATCH handler re-substitutes placeholders and pushes a new policy version to APIM. There is no state where the DB says one thing and APIM says another for long. The config drift detector will flag it if it happens.

## Budget enforcement contract

- Poll interval: 60 seconds
- Data source: Log Analytics `AzureDiagnostics` table, filtered by APIM resource ID and deployment name dimension
- Pricing source: hardcoded table first (known models: gpt-4o family, gpt-3.5-turbo, embeddings, o1 family), Azure Retail Prices API fallback for unknown models, with region fallback list (`eastus2`, `eastus`, `swedencentral`, `northcentralus`, `westus`, `francecentral`, `canadaeast`, `australiaeast`), batch/cached/FT SKU exclusion, prefer-lowest tie-break
- Trigger condition: `state == "active"` AND `mtd_cost_usd >= monthly_budget_usd * (threshold_pct / 100)`
- Action:
  1. APIM: PATCH subscription state -> `suspended`
  2. APIM: rewrite named value `watchtower-suspended-deployments` to append this deployment name
  3. DB: `state -> "suspended"`, add audit log entry
- Unsuspend: operator action only. No auto-unsuspend at month boundary (would be a footgun for endpoints legitimately over-budget).

Latency floor: LA ingestion (~30-90s) + worker interval (up to 60s) = ~90-150s from actual overspend to APIM lockout. This is not intended as the sole safeguard against a runaway loop; use APIM rate limits for hard rate ceilings and content safety for content-level controls.

## Compliance grading contract

Every deployment is scored against a curated subset of OWASP LLM Top 10 categories. Each category is a boolean pass/fail from real Azure state:

| Category | Pass condition (evidence) |
|---|---|
| LLM01 Prompt Injection | Content Safety Prompt Shields enabled on the account, OR content safety policy applied at APIM |
| LLM02 Insecure Output Handling | Response body-logging enabled at APIM diagnostics; output validation policy present |
| LLM04 Model DoS | Both TPM and RPM limits are non-null and > 0 |
| LLM05 Supply Chain | RAI policy applied to deployment; policy is `Microsoft.DefaultV2` or a customer-defined V2 policy |
| LLM06 Sensitive Info Disclosure | Content Safety account referenced by APIM policy; PII detection fragment present |
| LLM07 Insecure Plugin Design | (n/a for base model endpoints; scored as pass unless the endpoint declares plugins) |
| LLM08 Excessive Agency | MI-auth to Foundry (no API keys); APIM subscription-scoped keys with per-key rate limits |
| LLM09 Overreliance | Ownership metadata present (indicates human accountability); use-case description non-empty |
| LLM10 Model Theft | Key Vault RBAC enforced; subscription key stored only in KV, never in env vars or UI |

Grade thresholds:

- **A**: pass %= 100, no critical fails (LLM04, LLM05, LLM10)
- **B**: pass % 85-99, no critical fails
- **C**: pass % 70-84, or 1 critical fail
- **F**: pass % < 70, or 2+ critical fails

Grades update when Watchtower re-pushes policies, when the operator changes RAI or Content Safety config, or on demand via `POST /api/deployments/{name}/rescore`.

## Delete cascade contract

`DELETE /api/deployments/{name}` requires typed confirmation of the deployment name in the UI and executes:

1. APIM: DELETE subscription
2. APIM: DELETE API (which cascade-deletes operations + policy)
3. Key Vault: DELETE secret, then PURGE from soft-delete
4. Foundry: DELETE deployment (does NOT delete the project even if this is the last deployment in it)
5. DB: `state -> "deleted"` (record retained for audit; audit log entry added)
6. Named value `watchtower-suspended-deployments`: remove this deployment name if present

The cascade is best-effort: if step 2 fails, steps 3-6 still run. The audit log entry records exactly which steps succeeded. Partial state is safe (delete cascade is idempotent - re-running it will clean up remaining fragments).

`DELETE /api/projects/{name}` refuses if any deployments still reference the project. This is the one place Watchtower blocks a delete: dangling APIM APIs / KV secrets / Foundry deployments are worse than a stuck project name.

## Data retention

- DeploymentRecord: retained indefinitely (state moves to `"deleted"` on cascade; row remains for audit)
- AuditLog: retained indefinitely (no rotation, no compaction)
- Retail Prices cache: 24h in-process, evicted on restart
- Foundry model catalog: fetched per intake, not cached
- Log Analytics data: bounded by your workspace retention setting; Watchtower does not query beyond `retention_days`
- No PII is stored by Watchtower itself. All PII exposure is through APIM logs and Foundry itself; both are governed by your existing data classification policies.
