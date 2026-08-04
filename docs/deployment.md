# Deployment walkthrough

End-to-end steps to get AI Watchtower running against a real Azure environment.

## 0. Preflight

You need:

- An Azure subscription with quota for: 1 x App Service Plan B1, 1 x Postgres Flexible B1ms, 1 x user-assigned managed identity.
- **An existing resource group** with **all four** of these already provisioned:
  - Azure AI Foundry account (Cognitive Services, `kind = AIServices`)
  - API Management service (Developer tier or higher; use v2 for token-limit policies)
  - Key Vault with **RBAC authorization** enabled (not access policies)
  - Log Analytics workspace
- Client tooling: Azure CLI 2.60+, Bicep CLI (bundled with `az`), Node 20+, Python 3.12+, PowerShell 7+ (for the deploy script).
- **Owner** (or **Contributor + User Access Administrator**) on the target Foundry account. The Bicep grants cross-scope RBAC to Watchtower's managed identity, which requires User Access Administrator.

## 1. Wire APIM diagnostics into Log Analytics

Budget enforcement and monitoring both read from `ApiManagementGatewayLogs` and `AzureDiagnostics` tables. If APIM isn't sending diagnostic data to your workspace, Watchtower will show empty states everywhere.

```bash
az monitor diagnostic-settings create \
  --name watchtower-diag \
  --resource $(az apim show -g <rg> -n <apim-service> --query id -o tsv) \
  --workspace $(az monitor log-analytics workspace show -g <rg> -n <workspace> --query id -o tsv) \
  --logs '[{"categoryGroup":"allLogs","enabled":true}]' \
  --metrics '[{"category":"AllMetrics","enabled":true}]'
```

Enable **body logging** at the APIM API level for OpenAI APIs (needed for token metric emission):

```bash
az rest --method PUT \
  --uri "https://management.azure.com/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.ApiManagement/service/<apim>/diagnostics/applicationinsights?api-version=2023-05-01-preview" \
  --body '{
    "properties": {
      "loggerId": "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.ApiManagement/service/<apim>/loggers/<logger-name>",
      "sampling": {"samplingType": "fixed", "percentage": 100.0},
      "frontend": {"request": {"body": {"bytes": 8192}}, "response": {"body": {"bytes": 8192}}},
      "backend":  {"request": {"body": {"bytes": 8192}}, "response": {"body": {"bytes": 8192}}}
    }
  }'
```

(APIM's OpenTelemetry integration is the future-proof path; for now, App Insights logger + body capture is what the token metric policy needs.)

## 2. Deploy infrastructure

```powershell
cd infra
pwsh ./deploy.ps1 `
  -SubscriptionId 'YOUR-SUB-ID' `
  -ResourceGroup 'YOUR-RG' `
  -FoundryAccountName 'YOUR-FOUNDRY-ACCOUNT' `
  -ApimServiceName 'YOUR-APIM-SERVICE' `
  -KeyVaultName 'YOUR-KEY-VAULT' `
  -AppServicePlanSku 'B1' `
  -PostgresSku 'Standard_B1ms'
```

The script validates the template, then deploys. On success it prints:

- `webAppUrl` - where the app will be reachable
- `managedIdentityClientId` - the UAMI id used for all Azure control-plane calls
- `pgServerFqdn` - Postgres FQDN

## 3. Verify RBAC

The Bicep grants seven role assignments to the managed identity. Verify they landed:

```bash
MI_PRINCIPAL=$(az identity show -g <rg> -n id-watchtower-<suffix> --query principalId -o tsv)
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

## 4. Build and deploy the app

```powershell
# Frontend build (must run before backend packaging)
cd ..\frontend
npm install
npm run build
Copy-Item -Recurse -Force .\dist\* ..\backend\src\watchtower\static\

# Backend packaging
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
az webapp deployment list-publishing-credentials -g <rg> -n <web-app-name> --query publishingUserName -o tsv
# then GET https://<web-app-name>.scm.azurewebsites.net/api/deployments
```

or just wait 5-10 minutes and hit `https://<web-app-name>.azurewebsites.net/api/health`.

## 5. First intake

Open `https://<web-app-name>.azurewebsites.net/` in a browser. You should see the Projects page with an empty state.

Click **New project**, fill in:

- Foundry project name (e.g. `test-project`)
- Model + version + SKU (dropdowns are populated live from your Foundry catalog)
- Ownership fields (app name, owner, team, BU, env, cost center)
- TPM limit, RPM limit, monthly budget USD, threshold %

Submit. If everything is wired correctly, within ~30 seconds you will see the new endpoint in the Projects table with a Grade A compliance score and an APIM endpoint URL you can hit with:

```bash
KEY=$(az keyvault secret show --vault-name <kv> --name <secret-name> --query value -o tsv)
curl -H "api-key: $KEY" \
     -H "Content-Type: application/json" \
     -d '{"messages":[{"role":"user","content":"hello"}]}' \
     "https://<apim-gateway>/openai/deployments/<deployment>/chat/completions?api-version=2024-08-01-preview"
```

## 6. Verify enforcement

- Send a few requests. Within 60-90 seconds, the Projects table's **Consumed** column should show a non-zero cost and burn %.
- Set the budget threshold artificially low (e.g. 1%) via the deployment drawer. Send more requests. Within 60s, the endpoint state should flip to **Suspended** and subsequent requests should return 429.
- Check `/api/audit-log?deployment=<name>` - you should see an `auto_suspend` event with the cost that triggered it.

## Common pitfalls

- **Empty cost / burn %** - APIM diagnostics not enabled, or body logging not turned on. Watchtower shows an honest "no data yet" empty state rather than fake numbers. Fix step 1.
- **Intake fails at APIM stage with "Named Value cannot be empty"** - a Bicep dependency race; the `watchtower-suspended-deployments` named value did not get its placeholder. Run: `az apim nv update -g <rg> --service-name <apim> --named-value-id watchtower-suspended-deployments --value __none__`.
- **Intake fails at Foundry stage with 400** - Foundry account does not have `allowProjectManagement: true`. AI Watchtower attempts to enable this at first intake via the 2025-04-01-preview API; if that PATCH fails, do it manually with `az rest --method PATCH`.
- **Retail Prices returns $0 for a model** - Microsoft has not published prices for that SKU yet (common for preview models). The endpoint still works; cost / burn % show as "unknown" until pricing lands. Add the model to the hardcoded table in `retail_prices.py` as a stopgap.
- **App Service startup: `bash: startup.sh: No such file or directory`** - Oryx extracted your build under `/tmp/<hash>/` rather than `/home/site/wwwroot`. The shipped `startup.sh` handles this via `${BASH_SOURCE[0]}`; if you customised it, keep the dynamic APP_ROOT resolution.
