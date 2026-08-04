# AI Watchtower

**A governance, metering, and compliance control plane for Azure AI Foundry.**

AI Watchtower is a **layer on top of your existing Azure AI stack**. It does not replace Foundry, APIM, or Key Vault - it orchestrates them. If you already have those resources running and need standardised intake, budget enforcement, cost visibility, and compliance grading across every AI endpoint in your organisation, this is that missing control plane.

Not a mock. Not a demo. It talks to the real Azure control plane.

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

## Quick start

```bash
git clone https://github.com/<your-org>/ai-watchtower.git
cd ai-watchtower

# 1. Deploy infrastructure (creates MI, RBAC, App Service, Postgres)
cd infra
pwsh ./deploy.ps1 `
  -SubscriptionId <sub-id> `
  -ResourceGroup <rg> `
  -FoundryAccountName <foundry-account-name> `
  -ApimServiceName <apim-service-name> `
  -KeyVaultName <key-vault-name>

# 2. Build the frontend and stage it inside the backend package
cd ../frontend && npm install && npm run build
Copy-Item -Recurse ./dist/* ../backend/src/watchtower/static/

# 3. Package + deploy the app
cd ../backend
Compress-Archive -Path * -DestinationPath ../watchtower.zip -Force
az webapp deploy --resource-group <rg> --name <web-app-name> `
  --src-path ../watchtower.zip --type zip
```

Full walkthrough (Log Analytics wiring, APIM diagnostics, RAI policy, first intake) in [`docs/deployment.md`](docs/deployment.md).

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
