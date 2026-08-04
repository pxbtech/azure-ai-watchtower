# AI Watchtower

**A governance, metering, and compliance control plane for Azure AI Foundry.**

Not a mock. Not a demo. It talks to the real Azure control plane.

---

## Who this is for

AI Watchtower is built for teams who **already operate an Azure AI Hub with a gateway in front of it** - meaning Azure AI Foundry deployments published through Azure API Management, with keys stored in Key Vault and traffic logged to Log Analytics. If that describes your environment and you need standardised intake, per-endpoint budget enforcement, live cost tracking, and OWASP LLM compliance grading across everything the hub serves, this is the missing control plane.

**Not for:**
- Greenfield installs. Watchtower does not create Foundry / APIM / Key Vault for you.
- Single-model demos. It is overkill for one endpoint.
- Non-Azure AI stacks (OpenAI direct, Bedrock, self-hosted). Different control planes, different assumptions.

If any of those describe your setup, this repo is not the right fit.

---

## What this platform is for

Standardising how AI endpoints get provisioned, governed, priced, and audited on an existing Foundry + APIM hub. If your team is manually creating Foundry deployments, hand-editing APIM policies, and tracking ownership in a spreadsheet, Watchtower replaces all of that with one self-serve form and a background worker that enforces the rules programmatically.

In one sentence: **it is the intake + governance layer that a Foundry-behind-APIM hub needs but Azure does not ship.**

---

## What "a project" means in Watchtower, and the added value

A **project** in Watchtower is a Foundry project (logical grouping under a Foundry account) that hosts one or more **deployments**. Each deployment is one governed AI endpoint published through APIM. When a user submits the intake form, they either associate the new endpoint with an existing project or create a new one inline.

Projects give you organisational grouping: burn rate summed across all deployments in a project, per-project compliance rollups, and delete-project safety (refuses if any deployment still references it).

**The value versus doing this by hand today:**

| Without Watchtower | With Watchtower |
|---|---|
| Ticket-driven manual deployments (someone files a request, an engineer clicks through Foundry, another engineer configures APIM, a third stores the key in KV) | One-shot self-serve form: Foundry project + deployment + APIM API + subscription + KV secret, all in one submission, in about 30 seconds |
| Manual APIM policies per API, each one a snowflake | Standardised policy template applied identically to every endpoint: token limit, RPM, MI auth, metadata injection |
| Ownership tracked in a wiki or spreadsheet | Ownership metadata (app / owner / team / BU / env / cost center) injected as HTTP headers on every downstream call to Foundry |
| Reactive cost review at month-end | Real-time per-endpoint cost and burn %, auto-suspend at your configured threshold |
| Periodic manual OWASP audits | Continuous OWASP LLM Top 10 grading per model with critical-fail flags |
| Config changes to APIM policies or Foundry go untracked | Config drift detection from Azure Activity Log, surfaced per endpoint |
| Delete = "hope you cleaned up everything" | Delete cascade: APIM API + subscription + KV secret + soft-delete purge + audit log entry, single typed-confirmation |

Every endpoint that goes through Watchtower ends up in the same governance state - fully tagged, rate-limited, budget-capped, MI-authed to Foundry, compliance-graded - with zero manual APIM policy edits.

---

## Before you install: what you must already have

AI Watchtower assumes the following Azure resources already exist in one resource group. **It will not create them for you** - it configures them, orchestrates them, and grants its managed identity the right RBAC to manage them.

| Required resource | Why AI Watchtower needs it |
|---|---|
| **Azure AI Foundry account** (Cognitive Services, `kind = AIServices`) | Where AI Watchtower creates projects + model deployments and reads the model catalog. |
| **API Management service** (Developer tier or higher; **v2 SKU** for token-limit policies) | Where AI Watchtower publishes every deployment as a governed API with a hardened policy (rate limits, MI auth, metadata injection). |
| **Key Vault** (RBAC authorization mode, not access policies) | Where AI Watchtower stores each endpoint's APIM subscription key. Client apps retrieve keys from here via their own MI. |
| **Log Analytics workspace** (with APIM diagnostics wired in, body logging enabled) | Source of truth for real token consumption, cost calculation, and monitoring signals. Without this, cost / burn % / monitoring show empty states. |

Optional but recommended:

| Optional resource | What it unlocks |
|---|---|
| **Azure Content Safety account** | The `llm-content-safety` APIM policy fragment (prompt shields, PII detection) and better OWASP LLM01 / LLM06 grades. |
| **Existing Foundry Responsible AI policy (V2)** | Higher OWASP LLM05 (Supply Chain) score. AI Watchtower falls back to `Microsoft.DefaultV2` if you have not created one. |

Client tooling on the deploying machine: Azure CLI 2.60+, Bicep CLI, PowerShell 7+, Node 20+, Python 3.12+.

**Permissions:** the user running the Bicep deploy must be **Owner** on the target Foundry account (or **Contributor + User Access Administrator**). AI Watchtower grants its managed identity cross-scope RBAC, which requires User Access Administrator to assign.

If you do NOT have Foundry / APIM / Key Vault / Log Analytics already provisioned, provision those first. AI Watchtower is a governance layer, not a greenfield deployer.

---

## Manual configuration required on those resources

The Bicep provisions AI Watchtower's own resources (managed identity, App Service, Postgres) and grants RBAC. It does **not** touch the four prerequisite resources beyond RBAC. You must configure them yourself, once, before AI Watchtower will work end-to-end. Skipping any of these means the app will deploy but the corresponding feature will silently show empty states or fail on first use.

Each step below is a link to the exact command in [`docs/deployment.md`](docs/deployment.md).

| # | Manual step | Consequence if skipped |
|---|---|---|
| 1 | [Wire APIM diagnostics into Log Analytics](docs/deployment.md#1-wire-apim-diagnostics-into-log-analytics) (one `az monitor diagnostic-settings create` command) | Cost / burn % / monitoring pages show empty. Budget enforcement never fires. |
| 2 | [Enable body logging on APIM diagnostics](docs/deployment.md#2-enable-body-logging-on-apim-diagnostics) (one `az rest` PUT call) | `azure-openai-emit-token-metric` cannot see token counts. Cost stays at zero. |
| 3 | [Seed the `watchtower-suspended-deployments` APIM named value](docs/deployment.md#3-seed-the-apim-named-value) (one `az apim nv create` command) | **First intake fails** with "Named Value cannot be empty" - the suspend gate policy references this NV. |
| 4 | [Enable Foundry `allowProjectManagement`](docs/deployment.md#4-enable-foundry-project-management) (one `az rest` PATCH) | AI Watchtower tries to auto-enable this on first intake; if the account is missing Cognitive Services Contributor at that moment, first intake fails at the Foundry stage. |
| 5 | [Verify APIM SKU is v2](docs/deployment.md#5-verify-apim-sku) | `azure-openai-token-limit` policy silently no-ops on v1 tiers. Every intake will succeed but TPM enforcement will be inactive. |

Do these 5 things **before** running `deploy.ps1`. All five are one-liners and take maybe 5 minutes total.

---

## Security warning: no built-in authentication

**AI Watchtower ships with no authentication in front of the app itself.** If you deploy it and expose the URL publicly, anyone who finds it can create Foundry deployments in your subscription, spending real money against your budget.

Before making the app URL reachable from the internet, put App Service Authentication (Easy Auth) in front of it with Entra ID as the identity provider. See [`docs/deployment.md#7-optional-but-strongly-recommended-put-entra-id-auth-in-front`](docs/deployment.md#7-optional-but-strongly-recommended-put-entra-id-auth-in-front) for the exact commands (~2 minutes, no code changes needed).

Alternative interim mitigation: leave the App Service on the default `*.azurewebsites.net` hostname and restrict access with **Access Restrictions** (allow-list of your office IPs) until Easy Auth is in place.

---

## What it adds on top

Once installed, AI Watchtower gives you the controls that Foundry alone does not:

| Capability | Detail |
|---|---|
| One-shot intake | Foundry project -> deployment -> APIM API+subscription -> Key Vault secret, all in a single form submission. Full end-to-end pipeline in one click. |
| APIM enforcement | `azure-openai-token-limit`, `rate-limit-by-key`, `authentication-managed-identity`, `azure-openai-emit-token-metric`, metadata header injection (`X-App-Name`, `X-App-Owner`, `X-Cost-Center`, ...). |
| Budget enforcement | 60s worker polls Log Analytics token metrics, prices via Azure Retail Prices API, auto-suspends the endpoint at your configured threshold % (three-layer: APIM subscription + policy gate + named-value list). |
| Live cost | Per-deployment MTD cost + burn %, computed from real token consumption + real Retail prices (region fallback, batch/cached/FT exclusion, 24h cache). |
| OWASP LLM Top 10 | Per-model compliance grade (A / B / C / F) with pass %, critical-fail flag, and category-by-category evidence. |
| Config drift | Per-endpoint diff of "governed state" vs current Azure state, sourced from Azure Activity Log. |
| Delete cascade | Full cleanup: APIM API + subscription, KV secret + purge from soft-delete, DB record, audit log entry. Typed confirmation required. |
| PDF export | Per-endpoint billing report generated with reportlab. |

---

## Architecture

```
+---------------+        +---------------------+        +--------------------+
|   Browser     | -----> |  App Service        | -----> |  Azure AI Foundry  |
|   (React SPA) |        |  FastAPI + Worker   |        |  (Cognitive Svc)   |
+---------------+        |  UAMI               |        +--------------------+
                         |                     |
                         |                     | -----> |  API Management    |
                         |                     |        |  (policy + sub)    |
                         |                     |        +--------------------+
                         |                     |
                         |                     | -----> |  Key Vault         |
                         |                     |        |  (subscription key)|
                         |                     |        +--------------------+
                         |                     |
                         |                     | -----> |  PostgreSQL        |
                         |                     |        |  (governance DB)   |
                         |                     |        +--------------------+
                         |                     |
                         |                     | -----> |  Log Analytics     |
                         |                     |        |  (usage + drift)   |
                         +---------------------+        +--------------------+
```

More detail in [`docs/architecture.md`](docs/architecture.md).

---

## Deployment walkthrough

This is the abbreviated version. Every command has a verification step and full explanation in [`docs/deployment.md`](docs/deployment.md). Follow these in order.

### What you need to define before starting

Have these values ready. They map directly to Bicep parameters and `az` commands throughout:

| Placeholder | What it is | How to find it |
|---|---|---|
| `<sub>` | Azure subscription ID (GUID) | `az account show --query id -o tsv` |
| `<rg>` | Resource group containing all four Azure resources | Your existing hub RG |
| `<foundry>` | Foundry account name (Cognitive Services, `kind=AIServices`) | `az cognitiveservices account list -g <rg> --query "[?kind=='AIServices'].name" -o tsv` |
| `<apim>` | API Management service name | `az apim list -g <rg> --query "[].name" -o tsv` |
| `<kv>` | Key Vault name (must be RBAC mode) | `az keyvault list -g <rg> --query "[?properties.enableRbacAuthorization].name" -o tsv` |
| `<workspace>` | Log Analytics workspace name | `az monitor log-analytics workspace list -g <rg> --query "[].name" -o tsv` |

Also decide:

- **App Service Plan SKU** for Watchtower itself: `B1` for dev (~$13/mo, recommended), `P0V3` for prod (~$54/mo). `F1` Free exists but hits CPU quota under Azure SDK load - avoid.
- **Postgres SKU**: `Standard_B1ms` Burstable (~$12/mo, recommended for dev/small teams), `Standard_D2s_v3` General Purpose for larger deployments.
- **Azure region** for Watchtower's new resources: defaults to the RG's region. Watchtower itself is region-agnostic; Foundry / APIM / KV can be anywhere.

### Step 0: clone and log in

```bash
git clone https://github.com/pxbtech/azure-ai-watchtower.git
cd azure-ai-watchtower
az login
az account set --subscription <sub>
```

### Step 1: manual configuration on existing resources (5 minutes, one-time)

Five one-liners. Skip any and the corresponding feature silently fails. Full explanation and verification for each is in [`docs/deployment.md`](docs/deployment.md#part-a-manual-configuration-on-existing-resources).

```bash
# 1a. Wire APIM diagnostics into Log Analytics
az monitor diagnostic-settings create --name watchtower-diag \
  --resource $(az apim show -g <rg> -n <apim> --query id -o tsv) \
  --workspace $(az monitor log-analytics workspace show -g <rg> -n <workspace> --query id -o tsv) \
  --logs '[{"categoryGroup":"allLogs","enabled":true}]' \
  --metrics '[{"category":"AllMetrics","enabled":true}]'

# 1b. Enable body logging (see docs/deployment.md#2 for the az rest command;
#     needs an existing APIM logger of type applicationInsights)

# 1c. Seed the APIM named value that the suspend gate policy references
az apim nv create -g <rg> --service-name <apim> \
  --named-value-id watchtower-suspended-deployments \
  --display-name watchtower-suspended-deployments \
  --value __none__ --secret false

# 1d. Enable Foundry project management
az rest --method PATCH \
  --uri "https://management.azure.com/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<foundry>?api-version=2025-04-01-preview" \
  --body '{"properties": {"allowProjectManagement": true}}'

# 1e. Verify APIM is v2 (needed for token-limit policy)
az apim show -g <rg> -n <apim> --query "sku.name" -o tsv
# expected: Basicv2, Standardv2, or Premiumv2
```

### Step 2: deploy AI Watchtower's own infrastructure

Bicep provisions the user-assigned managed identity, cross-scope RBAC (7 role assignments), App Service Plan + Web App, and PostgreSQL Flexible Server.

```powershell
cd infra
pwsh ./deploy.ps1 `
  -SubscriptionId '<sub>' `
  -ResourceGroup '<rg>' `
  -FoundryAccountName '<foundry>' `
  -ApimServiceName '<apim>' `
  -KeyVaultName '<kv>' `
  -AppServicePlanSku 'B1' `
  -PostgresSku 'Standard_B1ms'
```

Takes ~10 minutes. On success, prints `webAppUrl`, `managedIdentityClientId`, and `pgServerFqdn`.

### Step 3: put Entra ID auth in front of the app (STRONGLY recommended)

Watchtower has no built-in authentication. Before the URL is reachable to anyone outside your control, add Easy Auth. Full command in [`docs/deployment.md`](docs/deployment.md#7-optional-but-strongly-recommended-put-entra-id-auth-in-front) - ~2 minutes.

Alternative interim: IP allow-list (`az webapp config access-restriction add`) if you can't do Entra right now.

### Step 4: build and deploy the app code

```powershell
# Build frontend, stage into backend package
cd ..\frontend
npm install
npm run build
New-Item -ItemType Directory -Force -Path ..\backend\src\watchtower\static | Out-Null
Copy-Item -Recurse -Force .\dist\* ..\backend\src\watchtower\static\

# Package and deploy backend
cd ..\backend
Compress-Archive -Path * -DestinationPath ..\watchtower.zip -Force
az webapp deploy `
  --resource-group <rg> `
  --name <web-app-name-from-step-2-output> `
  --src-path ..\watchtower.zip --type zip
```

Takes ~5-10 minutes. A 504 timeout from `az webapp deploy` is normal on B1 SKUs - Oryx keeps building server-side. Wait then hit `https://<web-app-name>.azurewebsites.net/api/health`.

### Step 5: first intake (smoke test)

Open `https://<web-app-name>.azurewebsites.net/`, click **New project**, fill the form (project name, model, SKU, ownership fields, TPM/RPM/budget/threshold). Within 30 seconds you should see the new endpoint appear in the Projects table with a Grade A compliance score.

Test the endpoint:

```bash
KEY=$(az keyvault secret show --vault-name <kv> --name <secret-name-from-ui> --query value -o tsv)
curl -H "api-key: $KEY" -H "Content-Type: application/json" \
     -d '{"messages":[{"role":"user","content":"hello"}]}' \
     "https://<apim-gateway>/openai/deployments/<deployment>/chat/completions?api-version=2024-08-01-preview"
```

Within 60-90 seconds the Projects table should show non-zero **Consumed** cost and burn %.

### Step 6: verify enforcement is actually firing

- Send a few more requests. Cost climbs.
- Lower the deployment's budget threshold to 1% via the drawer. Send more requests. Endpoint state flips to **Suspended** within ~60s. Requests start returning HTTP 429.
- `GET /api/audit-log?deployment=<name>` shows an `auto_suspend` event.

If any of that doesn't happen, see the [troubleshooting section](docs/deployment.md#troubleshooting) - it maps every likely error to the step that was skipped.

---

## Local development

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate    # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp ../.env.example ../.env                            # fill in your values
export $(cat ../.env | xargs)                         # PowerShell: see .env.example header
uvicorn watchtower.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev                                           # http://localhost:5173, proxied to :8000
```

Backend uses SQLite by default (`WATCHTOWER_DB_PATH`) unless `WATCHTOWER_DATABASE_URL` is set.

---

## Configuration

All settings are environment variables prefixed with `WATCHTOWER_`. See [`.env.example`](.env.example) for the full list and [`docs/configuration.md`](docs/configuration.md) for details.

The App Service Plan Bicep sets these automatically from Bicep parameters; you only need to worry about them for local dev.

---

## Repository layout

```
ai-watchtower/
├── backend/                    FastAPI app + background worker
│   ├── src/watchtower/
│   │   ├── routers/            HTTP routes (intake, projects, deployments, billing, ...)
│   │   ├── workers/            Budget enforcement worker (60s polling)
│   │   ├── policies/           APIM policy template with placeholders
│   │   ├── azure_clients.py    All Azure control-plane access (SDK + direct REST)
│   │   ├── retail_prices.py    Azure Retail Prices client with region fallback + caching
│   │   ├── config.py           Pydantic settings (WATCHTOWER_* env vars)
│   │   └── main.py             FastAPI app entrypoint, mounts SPA
│   ├── requirements.txt
│   └── startup.sh              App Service startup: run migrations + start uvicorn
├── frontend/                   React 18 + Vite + TS + TanStack Query
│   ├── src/pages/              Projects, Intake, Governance, Security, Billing, Monitoring
│   └── public/argon/           MIT-licensed Argon Dashboard theme (CSS + fonts)
├── infra/                      Bicep IaC
│   ├── main.bicep
│   ├── deploy.ps1
│   └── main.bicepparam.example
├── docs/                       Architecture, deployment, config, spec
├── .github/workflows/          CI: lint, typecheck, backend + frontend build
└── .env.example
```

---

## Security posture

- **Managed identity everywhere**: no keys in code, no keys in App Service settings. The Bicep grants the user-assigned MI only the RBAC roles it needs (Cognitive Services Contributor, Azure AI Developer, APIM Service Contributor, Key Vault Secrets Officer, Monitoring Reader, and User Access Administrator scoped to the Foundry account for assisted RBAC).
- **Key Vault RBAC** (not access policies). Secrets are versioned; delete cascade purges from soft-delete.
- **APIM subscription keys** are the only client credential; injected by AI Watchtower into KV at intake time, never surfaced in the UI.
- **HTTPS only**, TLS 1.2 minimum, FTPS disabled on the App Service.

See [`SECURITY.md`](SECURITY.md) for responsible disclosure.

---

## License

MIT. See [`LICENSE`](LICENSE).

Argon Dashboard theme assets under `frontend/public/argon/` are the MIT-licensed subset from Creative Tim; see their [license](https://www.creative-tim.com/license).
