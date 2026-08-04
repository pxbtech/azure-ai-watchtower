// =============================================================================
// AI Watchtower - infrastructure
//
// Deploys: user-assigned managed identity, cross-scope RBAC, App Service Plan +
// Web App (Python 3.12 Linux), PostgreSQL Flexible Server, and wires the KV
// reference for the Postgres connection string into the app settings.
//
// Assumes the following already exist in the target resource group:
//   - Azure AI Foundry / Cognitive Services (AIServices) account
//   - API Management service (Developer or higher; token-limit policies require v2+)
//   - Key Vault (RBAC authorization model)
//   - Log Analytics workspace (for APIM diagnostics + monitoring)
//
// Deploy at resource-group scope:
//   az deployment group create --resource-group <rg> --template-file main.bicep \
//     --parameters foundryAccountName=<name> apimServiceName=<name> keyVaultName=<name>
// =============================================================================

targetScope = 'resourceGroup'

@description('Location for new resources. Defaults to the RG location.')
param location string = resourceGroup().location

@description('Unique suffix for globally-scoped names (App Service, Postgres). Keep short.')
param nameSuffix string = uniqueString(resourceGroup().id, 'watchtower')

@description('REQUIRED: name of the existing Foundry (Microsoft.CognitiveServices/accounts, kind=AIServices) account.')
param foundryAccountName string

@description('REQUIRED: name of the existing APIM service (Microsoft.ApiManagement/service).')
param apimServiceName string

@description('REQUIRED: name of the existing Key Vault (Microsoft.KeyVault/vaults).')
param keyVaultName string

@description('App Service Plan SKU. F1=Free (fine for demos, hits CPU quota under Azure SDK load), B1=Basic (recommended), P0V3+ = production.')
@allowed(['F1', 'B1', 'B2', 'B3', 'P0V3', 'P1V3'])
param appServicePlanSku string = 'B1'

@description('PostgreSQL admin username.')
param pgAdminUser string = 'watchtower'

@description('PostgreSQL admin password. If unset, a deterministic password is generated from the RG/subscription IDs. Provide your own for production and store it in a secret store.')
@secure()
param pgAdminPassword string = 'Wt${uniqueString(resourceGroup().id, 'pg-admin', 'v1')}${uniqueString(subscription().subscriptionId, 'pg-v1')}!'

@description('PostgreSQL Flexible Server SKU. Standard_B1ms is Burstable (~$12/mo), Standard_D2s_v3 is General Purpose.')
param pgSku string = 'Standard_B1ms'

@description('PostgreSQL Flexible Server tier. Burstable / GeneralPurpose / MemoryOptimized.')
@allowed(['Burstable', 'GeneralPurpose', 'MemoryOptimized'])
param pgTier string = 'Burstable'

// ---- Well-known role IDs ----
var roleReader             = 'acdd72a7-3385-48ef-bd42-f606fba81ae7'
var roleCogSvcContributor  = '25fbc0a9-bd7c-42a3-aa1a-3b75d497ee68'
var roleAzureAiDeveloper   = '64702f94-c441-49e6-a78b-ef80e0188fee'
var roleUserAccessAdmin    = '18d7d88d-d35e-4fb5-a5c3-7773c20a72d9'
var roleApimServiceContrib = '312a565d-c81f-4fd8-895a-4e21e48d571c'
var roleKvSecretsOfficer   = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
var roleMonitoringReader   = '43d0d8ad-25c7-4714-9337-8ba259a9fe05'

// ---- Existing resources (referenced only) ----
resource foundry 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: foundryAccountName
}

resource apim 'Microsoft.ApiManagement/service@2023-05-01-preview' existing = {
  name: apimServiceName
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

// ---- User-assigned managed identity for AI Watchtower ----
resource watchtowerMi 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-watchtower-${nameSuffix}'
  location: location
}

// ---- Role assignments (all scoped narrowly to what the MI actually needs) ----
resource raReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, watchtowerMi.id, roleReader)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleReader)
    principalId: watchtowerMi.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource raCogSvcContrib 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, watchtowerMi.id, roleCogSvcContributor)
  scope: foundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleCogSvcContributor)
    principalId: watchtowerMi.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource raAiDev 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, watchtowerMi.id, roleAzureAiDeveloper)
  scope: foundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAzureAiDeveloper)
    principalId: watchtowerMi.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// User Access Administrator SCOPED to Foundry only.
// Lets Watchtower auto-grant APIM's MI its data-plane role on this Foundry account.
// Requires the deploying user to be Owner or User Access Administrator on the Foundry.
resource raUaa 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, watchtowerMi.id, roleUserAccessAdmin)
  scope: foundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleUserAccessAdmin)
    principalId: watchtowerMi.properties.principalId
    principalType: 'ServicePrincipal'
    description: 'AI Watchtower assisted RBAC: grant APIM MI data-plane role on this Foundry only'
  }
}

resource raApimContrib 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(apim.id, watchtowerMi.id, roleApimServiceContrib)
  scope: apim
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleApimServiceContrib)
    principalId: watchtowerMi.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource raKvOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, watchtowerMi.id, roleKvSecretsOfficer)
  scope: kv
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKvSecretsOfficer)
    principalId: watchtowerMi.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Monitoring Reader on the RG so the app can KQL against Log Analytics for security signals.
resource raMonitorReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, watchtowerMi.id, roleMonitoringReader)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleMonitoringReader)
    principalId: watchtowerMi.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---- PostgreSQL Flexible Server ----
var pgServerName = 'psql-watchtower-${nameSuffix}'
var pgDbName = 'watchtower'

resource pgServer 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: pgServerName
  location: location
  sku: { name: pgSku, tier: pgTier }
  properties: {
    version: '16'
    administratorLogin: pgAdminUser
    administratorLoginPassword: pgAdminPassword
    storage: { storageSizeGB: 32, autoGrow: 'Disabled' }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
  }
}

resource pgDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: pgServer
  name: pgDbName
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}

// Firewall: allow Azure services (App Service outbound). Tighten to specific IPs for production.
resource pgFwAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = {
  parent: pgServer
  name: 'AllowAllAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

// ---- App Service Plan ----
resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: 'asp-watchtower-${nameSuffix}'
  location: location
  sku: {
    name: appServicePlanSku
    tier: appServicePlanSku == 'F1' ? 'Free' : (startsWith(appServicePlanSku, 'B') ? 'Basic' : 'PremiumV3')
    capacity: 1
  }
  kind: 'linux'
  properties: { reserved: true }
}

// ---- Web App ----
resource web 'Microsoft.Web/sites@2024-04-01' = {
  name: 'app-watchtower-${nameSuffix}'
  location: location
  kind: 'app,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${watchtowerMi.id}': {} }
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      alwaysOn: appServicePlanSku != 'F1'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      appCommandLine: 'bash startup.sh'
      appSettings: [
        { name: 'WEBSITES_PORT', value: '8000' }
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'ENABLE_ORYX_BUILD', value: 'true' }
        { name: 'AZURE_CLIENT_ID', value: watchtowerMi.properties.clientId }
        { name: 'WATCHTOWER_MANAGED_IDENTITY_CLIENT_ID', value: watchtowerMi.properties.clientId }
        { name: 'WATCHTOWER_SUBSCRIPTION_ID', value: subscription().subscriptionId }
        { name: 'WATCHTOWER_RESOURCE_GROUP', value: resourceGroup().name }
        { name: 'WATCHTOWER_LOCATION', value: location }
        { name: 'WATCHTOWER_FOUNDRY_ACCOUNT', value: foundryAccountName }
        { name: 'WATCHTOWER_APIM_SERVICE', value: apimServiceName }
        { name: 'WATCHTOWER_KEY_VAULT', value: keyVaultName }
        { name: 'WATCHTOWER_DB_PATH', value: '/home/data/watchtower.db' }
        { name: 'WATCHTOWER_DATABASE_URL', value: 'postgresql+asyncpg://${pgAdminUser}:${uriComponent(pgAdminPassword)}@${pgServer.properties.fullyQualifiedDomainName}:5432/${pgDbName}?ssl=require' }
      ]
    }
  }
  dependsOn: [
    raReader
    raCogSvcContrib
    raAiDev
    raUaa
    raApimContrib
    raKvOfficer
    raMonitorReader
  ]
}

// ---- Outputs ----
output webAppName string = web.name
output webAppUrl string = 'https://${web.properties.defaultHostName}'
output managedIdentityClientId string = watchtowerMi.properties.clientId
output managedIdentityPrincipalId string = watchtowerMi.properties.principalId
output managedIdentityResourceId string = watchtowerMi.id
output pgServerFqdn string = pgServer.properties.fullyQualifiedDomainName
output pgDatabase string = pgDbName
