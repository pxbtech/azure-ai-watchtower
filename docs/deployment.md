# Deployment walkthrough

End-to-end steps to get AI Watchtower running against a real Azure environment. Follow them in order. Every manual step has the exact command inline - no "figure it out later" gaps.

## Time budget

- Manual prep on existing resources (steps 1-5): ~5 minutes
- Bicep deploy (step 6): ~10 minutes (Postgres is the slowest part)
- App code deploy (step 8): ~5-10 minutes (App Service Basic SKUs are slow to Oryx-build)
- Optional Easy Auth (step 7): ~2 minutes
- **Total, cold start: ~25 minutes**

## Prerequisites

Confirm you have all four resources listed in the [README prerequisite table](../README.md#before-you-install-what-you-must-already-have) and the client tooling installed. This walkthrough uses these placeholders throughout:

- `<sub>` = Azure subscription ID (GUID)
- `<rg>` = resource group containing all four Azure resources
- `<foundry>` = name of the Azure AI Foundry account (Cognitive Services, `kind=AIServices`)
- `<apim>` = name of the API Management service
- `<kv>` = name of the Key Vault (RBAC mode)
- `<workspace>` = name of the Log Analytics workspace

Set the active subscription once so subsequent commands don't need `--subscription`:

```bash
az login
az account set --subscription <sub>
```

---

# Part A: Manual configuration on existing resources

The Bicep does not touch your existing Foundry / APIM / KV / Log Analytics beyond RBAC grants. Do these five one-time steps first.

## 1. Wire APIM diagnostics into Log Analytics

Budget enforcement and monitoring read from `ApiManagementGatewayLogs` and `AzureDiagnostics` tables. If APIM isn't sending diagnostic data to your workspace, AI Watchtower will show empty states everywhere.

```bash
az monitor diagnostic-settings create \
  --name watchtower-diag \
  --resource $(az apim show -g <rg> -n <apim> --query id -o tsv) \
  --workspace $(az monitor log-analytics workspace show -g <rg> -n <workspace> --query id -o tsv) \
  --logs '[{"categoryGroup":"allLogs","enabled":true}]' \
  --metrics '[{"category":"AllMetrics","enabled":true}]'
```

Verify:

```bash
az monitor diagnostic-settings list --resource $(az apim show -g <rg> -n <apim> --query id -o tsv) -o table
```

## 2. Enable body logging on APIM diagnostics

The `azure-openai-emit-token-metric` policy reads token counts out of the OpenAI response body. Without body capture at the APIM diagnostics layer, token counts never reach Log Analytics and cost calculations return zero.

First, ensure there is an APIM logger of type `applicationInsights` or `azureMonitor`. If you don't have one:

```bash
az apim logger create \
  -g <rg> --service-name <apim> \
  --logger-id watchtower-logger \
  --logger-type applicationInsights \
  --credentials "instrumentationKey=<app-insights-instrumentation-key>"
```

Then enable body capture on the diagnostic:

```bash
az rest --method PUT \
  --uri "https://management.azure.com/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.ApiManagement/service/<apim>/diagnostics/applicationinsights?api-version=2023-05-01-preview" \
  --body '{
    "properties": {
      "loggerId": "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.ApiManagement/service/<apim>/loggers/watchtower-logger",
      "sampling": {"samplingType": "fixed", "percentage": 100.0},
      "alwaysLog": "allErrors",
      "frontend": {"request": {"body": {"bytes": 8192}}, "response": {"body": {"bytes": 8192}}},
      "backend":  {"request": {"body": {"bytes": 8192}}, "response": {"body": {"bytes": 8192}}}
    }
  }'
```

Verify:

```bash
az rest --method GET \
  --uri "https://management.azure.com/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.ApiManagement/service/<apim>/diagnostics/applicationinsights?api-version=2023-05-01-preview" \
  --query "properties.frontend.response.body.bytes"
# expected: 8192
```

## 3. Seed the APIM named value

AI Watchtower's suspend gate policy references an APIM named value called `watchtower-suspended-deployments`. It has to exist before the first intake attempts to reference it, and APIM rejects named values with empty values, so we seed it with a sentinel string.

```bash
az apim nv create \
  -g <rg> --service-name <apim> \
  --named-value-id watchtower-suspended-deployments \
  --display-name watchtower-suspended-deployments \
  --value __none__ \
  --secret false
```

Verify:

```bash
az apim nv show \
  -g <rg> --service-name <apim> \
  --named-value-id watchtower-suspended-deployments \
  --query "{name:displayName, value:value}"
# expected: {"name": "watchtower-suspended-deployments", "value": "__none__"}
```

## 4. Enable Foundry project management

The Foundry account needs `allowProjectManagement: true` before AI Watchtower can create projects on it. The intake router attempts to auto-set this on first use via a PATCH, but that only works if the caller has Cognitive Services Contributor at that moment. Setting it manually up front avoids a first-intake failure.

```bash
az rest --method PATCH \
  --uri "https://management.azure.com/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<foundry>?api-version=2025-04-01-preview" \
  --body '{"properties": {"allowProjectManagement": true}}'
```

Verify:

```bash
az rest --method GET \
  --uri "https://management.azure.com/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<foundry>?api-version=2025-04-01-preview" \
  --query "properties.allowProjectManagement"
# expected: true
```

## 5. Verify APIM SKU

The `azure-openai-token-limit` policy requires APIM v2 (Standardv2, Premiumv2, or Basicv2). On v1 SKUs (Developer, Basic, Standard, Premium, Consumption), the policy element is not recognised and TPM enforcement silently no-ops. RPM enforcement via `rate-limit-by-key` still works.

```bash
az apim show -g <rg> -n <apim> --query "sku.name" -o tsv
```

Expected: `Basicv2`, `Standardv2`, or `Premiumv2`. If you see `Developer`, `Basic`, `Standard`, `Premium`, or `Consumption`, plan a SKU migration or accept RPM-only enforcement (edit the APIM policy template in `backend/src/watchtower/policies/main_policy.xml` to remove the token-limit element).

---

# Part B: Deploy AI Watchtower

## 6. Deploy infrastructure

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

The script validates the template, then deploys. On success it prints:

- `webAppUrl` - where the app will be reachable
- `managedIdentityClientId` - the UAMI id used for all Azure control-plane calls
- `pgServerFqdn` - Postgres FQDN

If the deploy fails on a role assignment ("AuthorizationFailed"), you are missing User Access Administrator on the Foundry account. Have your Azure admin grant it, or run the Bicep as an Owner of the RG.

### Verify RBAC landed correctly

The Bicep grants seven role assignments to the managed identity. Verify they landed:

```bash
MI_PRINCIPAL=$(az identity show -g <rg> -n id-watchtower-<suffix-from-bicep-output> --query principalId -o tsv)
az role assignment list --assignee $MI_PRINCIPAL --all -o table
```

Expected roles (target scope in parentheses):

| Role | Scope |
|---|---|
| Reader | RG |
| Cognitive Services Contributor | Foundry account |
| Azure AI Developer | Foundry account |
| User Access Administrator | **Foundry account only** (not RG) |
| API Management Service Contributor | APIM |
| Key Vault Secrets Officer | Key Vault |
| Monitoring Reader | RG |

## 7. Optional but strongly recommended: put Entra ID auth in front

AI Watchtower has no built-in authentication. Before making the app URL reachable to anyone outside your control, add App Service Authentication (Easy Auth) with Entra ID:

```bash
# Register an Entra ID app for the web app
APP_ID=$(az ad app create \
  --display-name "AI Watchtower - <rg>" \
  --sign-in-audience AzureADMyOrg \
  --web-redirect-uris "https://<web-app-name>.azurewebsites.net/.auth/login/aad/callback" \
  --query appId -o tsv)

# Enable App Service Authentication with Entra as the provider
az webapp auth update \
  -g <rg> -n <web-app-name> \
  --enabled true \
  --action RedirectToLoginPage \
  --redirect-provider AzureActiveDirectory \
  --aad-client-id $APP_ID \
  --aad-token-issuer-url "https://sts.windows.net/$(az account show --query tenantId -o tsv)/"
```

Anyone hitting the web app URL will now be redirected to sign in with an Entra account in your tenant.

**Alternative interim mitigation** if you can't set up Easy Auth right now: lock the app down to specific IPs.

```bash
az webapp config access-restriction add \
  -g <rg> -n <web-app-name> \
  --rule-name office-ips \
  --action Allow \
  --ip-address <your-office-cidr> \
  --priority 100

az webapp config access-restriction add \
  -g <rg> -n <web-app-name> \
  --rule-name deny-all \
  --action Deny \
  --ip-address 0.0.0.0/0 \
  --priority 65000
```

## 8. Build and deploy the app

```powershell
# Frontend build (must run before backend packaging)
cd ..\frontend
npm install
npm run build

# Stage the built SPA inside the backend package
New-Item -ItemType Directory -Force -Path ..\backend\src\watchtower\static | Out-Null
Copy-Item -Recurse -Force .\dist\* ..\backend\src\watchtower\static\

# Package the backend
cd ..\backend
Compress-Archive -Path * -DestinationPath ..\watchtower.zip -Force

# Deploy
az webapp deploy `
  --resource-group <rg> `
  --name <web-app-name-from-bicep-output> `
  --src-path ..\watchtower.zip `
  --type zip
```

`az webapp deploy` proxies through Kudu and often times out (504) at ~5 minutes on Basic SKUs, even though the server-side Oryx build continues. If you get a 504, poll the deployment status:

```bash
# Get the latest deployment status
az webapp deployment list -g <rg> -n <web-app-name> --query "[0].{id:id, status:status, complete:complete, time:endTime}"
```

Or just wait 5-10 minutes and hit the health endpoint:

```bash
curl https://<web-app-name>.azurewebsites.net/api/health
# expected: {"status":"ok"}
```

## 9. First intake (smoke test)

Open `https://<web-app-name>.azurewebsites.net/` in a browser. You should see the Projects page with an empty state.

Click **New project**, fill in:

- Foundry project name (e.g. `test-project`)
- Model + version + SKU (dropdowns are populated live from your Foundry catalog)
- Ownership fields (app name, owner, team, business unit, environment, cost center)
- TPM limit, RPM limit, monthly budget USD, threshold %

Submit. If everything is wired correctly, within ~30 seconds you will see the new endpoint in the Projects table with a Grade A compliance score and an APIM endpoint URL you can hit with:

```bash
KEY=$(az keyvault secret show --vault-name <kv> --name <secret-name-shown-in-ui> --query value -o tsv)
curl -H "api-key: $KEY" \
     -H "Content-Type: application/json" \
     -d '{"messages":[{"role":"user","content":"hello"}]}' \
     "https://<apim-gateway>/openai/deployments/<deployment>/chat/completions?api-version=2024-08-01-preview"
```

## 10. Verify enforcement is actually firing

- Send a few requests through the APIM endpoint. Within 60-90 seconds, the Projects table's **Consumed** column should show a non-zero cost and burn %.
- Set the budget threshold artificially low (e.g. 1%) via the deployment drawer. Send more requests. Within 60 seconds, the endpoint state should flip to **Suspended** and subsequent requests should return HTTP 429.
- Check `GET /api/audit-log?deployment=<name>` - you should see an `auto_suspend` event with the cost that triggered it.

If cost stays at zero for more than 5 minutes despite request traffic, check the [troubleshooting section](#troubleshooting) below.

---

# Troubleshooting

## Empty cost / burn % / monitoring page

Root causes, in order of likelihood:

1. **Step 1 skipped**: APIM diagnostics not sending to Log Analytics. Fix: run the step 1 command.
2. **Step 2 skipped**: Body logging not enabled on APIM diagnostics. Token metrics need this. Fix: run the step 2 command.
3. **Log Analytics ingestion lag**: normal delay is 30-90 seconds; up to 5 minutes on cold workspaces. Wait 5 minutes then reload.
4. **Wrong workspace**: the diagnostic setting from step 1 points at a workspace AI Watchtower's MI doesn't have Monitoring Reader on. Check `az role assignment list --assignee <mi-principal>` includes Monitoring Reader.

## Intake fails at APIM stage with "Named Value cannot be empty"

Step 3 skipped. Run:

```bash
az apim nv create -g <rg> --service-name <apim> \
  --named-value-id watchtower-suspended-deployments \
  --display-name watchtower-suspended-deployments \
  --value __none__ --secret false
```

## Intake fails at Foundry stage with 400 "allowProjectManagement"

Step 4 skipped and the auto-set fallback also failed (usually because Bicep's role assignments haven't propagated yet, or the deploying user is missing Cognitive Services Contributor).

Wait 5 minutes for RBAC propagation, then either retry the intake or run step 4 manually.

## Retail Prices returns $0 for a model

Microsoft has not published prices for that SKU yet (common for preview models). The endpoint still works; cost / burn % show as "unknown" until pricing lands. As a stopgap, add the model to the hardcoded table in `backend/src/watchtower/retail_prices.py::MODEL_PRICING_PER_1M`.

## App Service startup: `bash: startup.sh: No such file or directory`

Oryx extracted your build under `/tmp/<hash>/` rather than `/home/site/wwwroot`. The shipped `startup.sh` handles this via `${BASH_SOURCE[0]}`; if you customised it, keep the dynamic `APP_ROOT` resolution.

## App Service startup: `ModuleNotFoundError: No module named 'six'`

The `azure-mgmt-resourcegraph` package has an undeclared runtime dependency on `six`. The shipped `requirements.txt` includes `six`, `msrest`, and `msrestazure` explicitly. If you removed them, put them back.

## PATCH deployment fails with "Named Value 'watchtower-suspended-deployments' does not exist"

Step 3 skipped. See above.

## First intake succeeds but TPM limit not enforced

APIM SKU is v1. Only RPM enforcement is active. Migrate to v2 (Standardv2 / Premiumv2 / Basicv2) or accept RPM-only.
