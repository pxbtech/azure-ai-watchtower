#!/usr/bin/env pwsh
# Deploy AI Watchtower infrastructure to an Azure resource group.
#
# Prerequisites:
#   - Existing Azure AI Foundry account (Cognitive Services, kind=AIServices)
#   - Existing API Management service (Developer tier or higher)
#   - Existing Key Vault (RBAC authorization model)
#   - Existing Log Analytics workspace (used for APIM diagnostics)
#   - The user running this script must be Owner (or User Access Administrator +
#     Contributor) on the target Foundry account and the resource group.
#
# Usage:
#   ./deploy.ps1 -SubscriptionId <id> -ResourceGroup <rg> `
#                -FoundryAccountName <name> -ApimServiceName <name> -KeyVaultName <name>

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)] [string]$SubscriptionId,
  [Parameter(Mandatory = $true)] [string]$ResourceGroup,
  [Parameter(Mandatory = $true)] [string]$FoundryAccountName,
  [Parameter(Mandatory = $true)] [string]$ApimServiceName,
  [Parameter(Mandatory = $true)] [string]$KeyVaultName,
  [ValidateSet('F1','B1','B2','B3','P0V3','P1V3')] [string]$AppServicePlanSku = 'B1',
  [ValidateSet('Standard_B1ms','Standard_B2s','Standard_D2s_v3')] [string]$PostgresSku = 'Standard_B1ms'
)

$ErrorActionPreference = 'Stop'

Write-Host "Setting subscription..." -ForegroundColor Cyan
az account set --subscription $SubscriptionId | Out-Null

Write-Host "Validating template..." -ForegroundColor Cyan
$validation = az deployment group validate `
  --resource-group $ResourceGroup `
  --template-file "$PSScriptRoot/main.bicep" `
  --parameters `
    foundryAccountName=$FoundryAccountName `
    apimServiceName=$ApimServiceName `
    keyVaultName=$KeyVaultName `
    appServicePlanSku=$AppServicePlanSku `
    pgSku=$PostgresSku `
  -o json | ConvertFrom-Json

if ($validation.error) {
  Write-Host "Validation failed:" -ForegroundColor Red
  $validation.error | ConvertTo-Json -Depth 10 | Write-Host
  exit 1
}
Write-Host "Validation passed." -ForegroundColor Green

Write-Host "Deploying (creates MI, RBAC, App Service, Postgres)..." -ForegroundColor Cyan
$deploy = az deployment group create `
  --resource-group $ResourceGroup `
  --template-file "$PSScriptRoot/main.bicep" `
  --parameters `
    foundryAccountName=$FoundryAccountName `
    apimServiceName=$ApimServiceName `
    keyVaultName=$KeyVaultName `
    appServicePlanSku=$AppServicePlanSku `
    pgSku=$PostgresSku `
  --name "watchtower-$(Get-Date -Format 'yyyyMMdd-HHmmss')" `
  -o json | ConvertFrom-Json

if ($LASTEXITCODE -ne 0) {
  Write-Host "Deployment failed." -ForegroundColor Red
  exit 1
}

Write-Host ""
Write-Host "Deployment succeeded." -ForegroundColor Green
Write-Host "Web app URL     : $($deploy.properties.outputs.webAppUrl.value)" -ForegroundColor White
Write-Host "MI client id    : $($deploy.properties.outputs.managedIdentityClientId.value)" -ForegroundColor White
Write-Host "MI principal id : $($deploy.properties.outputs.managedIdentityPrincipalId.value)" -ForegroundColor White
Write-Host "Postgres FQDN   : $($deploy.properties.outputs.pgServerFqdn.value)" -ForegroundColor White
Write-Host ""
Write-Host "Next: build + deploy app code." -ForegroundColor Yellow
Write-Host "  npm --prefix ../frontend install && npm --prefix ../frontend run build" -ForegroundColor Gray
Write-Host "  cd ../backend && Compress-Archive -Path * -DestinationPath ../watchtower.zip -Force" -ForegroundColor Gray
Write-Host "  az webapp deploy --resource-group $ResourceGroup --name $($deploy.properties.outputs.webAppName.value) --src-path ../watchtower.zip --type zip" -ForegroundColor Gray
