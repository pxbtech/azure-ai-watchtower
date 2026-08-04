"""All Azure SDK interactions live here. Routers depend on these clients, never on the SDKs directly."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import QueryRequest
from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient
from azure.mgmt.apimanagement import ApiManagementClient
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.monitor import MonitorManagementClient
from azure.keyvault.secrets import SecretClient
from azure.monitor.query import LogsQueryClient, LogsQueryStatus
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from .config import get_settings

settings = get_settings()


def get_credential():
    """Prefer user-assigned MI in App Service; fall back to DefaultAzureCredential for local dev."""
    client_id = settings.managed_identity_client_id or os.getenv("AZURE_CLIENT_ID")
    if client_id and os.getenv("WEBSITE_SITE_NAME"):  # running in App Service
        return ManagedIdentityCredential(client_id=client_id)
    return DefaultAzureCredential(exclude_interactive_browser_credential=False)


_credential = None


def credential():
    global _credential
    if _credential is None:
        _credential = get_credential()
    return _credential


# ============================================================================
# Discovery: Azure Resource Graph
# ============================================================================
class DiscoveryClient:
    def __init__(self):
        self.client = ResourceGraphClient(credential())

    def resources(self) -> list[dict[str, Any]]:
        """Discovers Foundry, APIM, KV in the target subscription."""
        query = """
        resources
        | where type in~ (
            'microsoft.cognitiveservices/accounts',
            'microsoft.apimanagement/service',
            'microsoft.keyvault/vaults'
          )
        | project id, name, type, kind, location, resourceGroup,
                  sku = tostring(sku.name),
                  localAuthDisabled = tobool(properties.disableLocalAuth),
                  publicNetworkAccess = tostring(properties.publicNetworkAccess),
                  allowProjectManagement = tobool(properties.allowProjectManagement),
                  tags
        | order by type asc, name asc
        """
        req = QueryRequest(subscriptions=[settings.subscription_id], query=query)
        resp = self.client.resources(req)
        return list(resp.data) if resp.data else []


# ============================================================================
# Foundry (Cognitive Services)
# ============================================================================
class FoundryClient:
    def __init__(self):
        self.client = CognitiveServicesManagementClient(credential(), settings.subscription_id)

    def get_account(self, account_name: str | None = None) -> dict[str, Any]:
        name = account_name or settings.foundry_account
        acct = self.client.accounts.get(settings.resource_group, name)
        return {
            "name": acct.name,
            "kind": acct.kind,
            "location": acct.location,
            "sku": acct.sku.name if acct.sku else None,
            "endpoint": acct.properties.endpoint if acct.properties else None,
            "custom_subdomain": (
                acct.properties.custom_sub_domain_name if acct.properties else None
            ),
            "disable_local_auth": (
                acct.properties.disable_local_auth if acct.properties else None
            ),
            "public_network_access": (
                acct.properties.public_network_access if acct.properties else None
            ),
            "allow_project_management": (
                getattr(acct.properties, "allow_project_management", None)
                if acct.properties else None
            ),
            "identity_principal_id": (
                acct.identity.principal_id if acct.identity else None
            ),
        }

    def list_available_models(self) -> list[dict[str, Any]]:
        """Returns models deployable in this account's region, PTU filtered out.
        Uses direct ARM REST - the SDK method signatures shift between versions
        and silently return nothing on modern Foundry API surfaces."""
        import httpx
        try:
            token = credential().get_token("https://management.azure.com/.default").token
        except Exception:
            return []
        url = (
            f"https://management.azure.com/subscriptions/{settings.subscription_id}"
            f"/providers/Microsoft.CognitiveServices/locations/{settings.location}"
            f"/models?api-version=2024-10-01"
        )
        try:
            with httpx.Client(timeout=60) as c:
                r = c.get(url, headers={"Authorization": f"Bearer {token}"})
            if r.status_code >= 400:
                return []
            data = r.json()
        except Exception:
            return []

        result: list[dict[str, Any]] = []
        for m in data.get("value", []):
            if m.get("kind") not in ("OpenAI", "AIServices"):
                continue
            model = m.get("model") or {}
            if not model.get("name"):
                continue
            skus = []
            for sku in (model.get("skus") or []):
                sku_name = sku.get("name")
                if not sku_name or sku_name in (
                    "ProvisionedManaged", "GlobalProvisionedManaged", "DataZoneProvisionedManaged",
                ):
                    continue
                capacity = sku.get("capacity") or {}
                skus.append({
                    "name": sku_name,
                    "usage_name": sku.get("usageName"),
                    "capacity_default": capacity.get("default"),
                    "capacity_min": capacity.get("minimum"),
                    "capacity_max": capacity.get("maximum"),
                })
            if not skus:
                continue
            deprecation = model.get("deprecation") or {}
            result.append({
                "name": model["name"],
                "version": model.get("version"),
                "format": model.get("format"),
                "skus": skus,
                "lifecycle_status": model.get("lifecycleStatus"),
                "deprecation_inference": deprecation.get("inference"),
                "deprecation_fine_tune": deprecation.get("fineTune"),
                "capabilities": model.get("capabilities") or {},
            })
        return result

    def list_deployments(self, account_name: str | None = None) -> list[dict[str, Any]]:
        name = account_name or settings.foundry_account
        deps = self.client.deployments.list(settings.resource_group, name)
        result = []
        for d in deps:
            p = d.properties
            result.append({
                "name": d.name,
                "model_name": p.model.name if p and p.model else None,
                "model_version": p.model.version if p and p.model else None,
                "sku_name": d.sku.name if d.sku else None,
                "capacity": d.sku.capacity if d.sku else None,
                "rai_policy_name": p.rai_policy_name if p else None,
                "provisioning_state": p.provisioning_state if p else None,
            })
        return result

    def list_projects(self, account_name: str | None = None) -> list[dict[str, Any]]:
        """List Foundry projects (Cognitive Services /projects child resource)."""
        name = account_name or settings.foundry_account
        import httpx
        token = credential().get_token("https://management.azure.com/.default").token
        url = (
            f"https://management.azure.com/subscriptions/{settings.subscription_id}"
            f"/resourceGroups/{settings.resource_group}"
            f"/providers/Microsoft.CognitiveServices/accounts/{name}"
            f"/projects?api-version=2025-04-01-preview"
        )
        with httpx.Client(timeout=30) as client:
            r = client.get(url, headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 404:
            return []
        r.raise_for_status()
        items = r.json().get("value", [])
        return [{
            "name": (it.get("name") or "").split("/")[-1],
            "location": it.get("location"),
            "properties": it.get("properties", {}),
        } for it in items]

    def create_project(
        self,
        project_name: str,
        display_name: str | None = None,
        description: str | None = None,
        account_name: str | None = None,
    ) -> dict[str, Any]:
        """PUT a Foundry project. Requires the Foundry account to have
        allowProjectManagement=true and the account itself to have a managed identity.
        Uses 2025-04-01-preview which requires identity block on the project too."""
        name = account_name or settings.foundry_account
        import httpx
        token = credential().get_token("https://management.azure.com/.default").token
        url = (
            f"https://management.azure.com/subscriptions/{settings.subscription_id}"
            f"/resourceGroups/{settings.resource_group}"
            f"/providers/Microsoft.CognitiveServices/accounts/{name}"
            f"/projects/{project_name}?api-version=2025-04-01-preview"
        )
        body = {
            "location": settings.location,
            "identity": {"type": "SystemAssigned"},
            "properties": {
                "displayName": display_name or project_name,
                **({"description": description} if description else {}),
            },
        }
        with httpx.Client(timeout=120) as client:
            r = client.put(url, headers={"Authorization": f"Bearer {token}"}, json=body)
        if r.status_code >= 400:
            # Surface the actual Azure error message, not just an MDN link
            try:
                err = r.json().get("error", {})
                raise RuntimeError(f"[{err.get('code', r.status_code)}] {err.get('message', r.text)}")
            except ValueError:
                raise RuntimeError(f"[{r.status_code}] {r.text}")
        return r.json()

    def ensure_rai_policy(
        self,
        policy_name: str = "watchtower-balanced",
        account_name: str | None = None,
        model_format: str | None = None,
        model_name: str | None = None,
    ) -> str:
        """Return the RAI policy name to bind at deployment time.
        Embedding models don't accept content filters, so return the platform default for them.
        On any failure creating the custom policy, fall back to Microsoft.DefaultV2 so a governed
        deployment still ships with a bound RAI (never unfiltered)."""
        name = account_name or settings.foundry_account

        # Embedding / non-chat models: content filters don't apply
        if model_name and ("embedding" in model_name.lower()):
            return "Microsoft.DefaultV2"
        if model_format and model_format.lower() not in ("openai",):
            return "Microsoft.DefaultV2"

        try:
            existing = self.client.rai_policies.get(settings.resource_group, name, policy_name)
            return existing.name
        except ResourceNotFoundError:
            pass
        except HttpResponseError:
            return "Microsoft.DefaultV2"

        from azure.mgmt.cognitiveservices.models import RaiPolicy, RaiPolicyProperties, RaiPolicyContentFilter

        filters = []
        for cat in ("Hate", "Sexual", "Violence", "Selfharm"):
            for src in ("Prompt", "Completion"):
                filters.append(RaiPolicyContentFilter(
                    name=cat, enabled=True, blocking=True,
                    severity_threshold="Medium", source=src,
                ))
        filters.append(RaiPolicyContentFilter(name="Jailbreak", enabled=True, blocking=True, source="Prompt"))
        filters.append(RaiPolicyContentFilter(name="Indirect Attack", enabled=True, blocking=True, source="Prompt"))
        filters.append(RaiPolicyContentFilter(name="Protected Material Text", enabled=True, blocking=True, source="Completion"))
        filters.append(RaiPolicyContentFilter(name="Protected Material Code", enabled=True, blocking=False, source="Completion"))

        policy = RaiPolicy(properties=RaiPolicyProperties(
            base_policy_name="Microsoft.DefaultV2",
            mode="Blocking",
            content_filters=filters,
        ))
        try:
            result = self.client.rai_policies.create_or_update(
                settings.resource_group, name, policy_name, policy,
            )
            return result.name
        except HttpResponseError:
            return "Microsoft.DefaultV2"

    def create_deployment(
        self,
        deployment_name: str,
        model_name: str,
        model_version: str,
        model_format: str = "OpenAI",
        sku_name: str = "GlobalStandard",
        capacity: int = 1,
        rai_policy_name: str = "watchtower-balanced",
        account_name: str | None = None,
    ) -> dict[str, Any]:
        name = account_name or settings.foundry_account
        rai = self.ensure_rai_policy(rai_policy_name, name, model_format=model_format, model_name=model_name)
        if not rai:
            raise ValueError("RAI policy is required. AI Watchtower rejects deployments without one.")

        from azure.mgmt.cognitiveservices.models import Deployment, DeploymentProperties, DeploymentModel, Sku

        dep = Deployment(
            sku=Sku(name=sku_name, capacity=capacity),
            properties=DeploymentProperties(
                model=DeploymentModel(format=model_format, name=model_name, version=model_version),
                rai_policy_name=rai,
            ),
        )
        try:
            poller = self.client.deployments.begin_create_or_update(
                settings.resource_group, name, deployment_name, dep,
            )
            result = poller.result()
        except HttpResponseError as e:
            # Bubble up Azure's error code + message verbatim so the UI can show the real reason
            code = getattr(e, "error", None) and e.error.code or e.status_code
            msg = getattr(e, "error", None) and e.error.message or str(e)
            raise RuntimeError(f"[{code}] {msg}") from e
        return {
            "name": result.name,
            "sku": {"name": result.sku.name, "capacity": result.sku.capacity},
            "rai_policy_name": result.properties.rai_policy_name,
            "provisioning_state": result.properties.provisioning_state,
        }

    def delete_deployment(self, deployment_name: str, account_name: str | None = None) -> bool:
        """Delete a Foundry deployment. Returns True if delete succeeded or resource was already gone."""
        name = account_name or settings.foundry_account
        try:
            poller = self.client.deployments.begin_delete(settings.resource_group, name, deployment_name)
            poller.result()
            return True
        except ResourceNotFoundError:
            return True
        except Exception:
            return False

    def delete_project(self, project_name: str, account_name: str | None = None) -> tuple[bool, str]:
        """Delete a Foundry project via REST (SDK support varies). Returns (success, msg)."""
        name = account_name or settings.foundry_account
        import httpx
        try:
            token = credential().get_token("https://management.azure.com/.default").token
        except Exception as e:
            return False, f"auth: {e}"
        url = (
            f"https://management.azure.com/subscriptions/{settings.subscription_id}"
            f"/resourceGroups/{settings.resource_group}"
            f"/providers/Microsoft.CognitiveServices/accounts/{name}"
            f"/projects/{project_name}?api-version=2025-04-01-preview"
        )
        try:
            with httpx.Client(timeout=120) as c:
                r = c.delete(url, headers={"Authorization": f"Bearer {token}"})
            if r.status_code in (200, 202, 204):
                return True, "deleted"
            if r.status_code == 404:
                return True, "already gone"
            return False, f"HTTP {r.status_code}: {r.text[:400]}"
        except Exception as e:
            return False, str(e)

    def get_endpoint(self, account_name: str | None = None) -> str:
        name = account_name or settings.foundry_account
        acct = self.client.accounts.get(settings.resource_group, name)
        return acct.properties.endpoint


# ============================================================================
# APIM
# ============================================================================
class ApimClient:
    def __init__(self):
        self.client = ApiManagementClient(credential(), settings.subscription_id)

    def get_service(self) -> dict[str, Any]:
        s = self.client.api_management_service.get(settings.resource_group, settings.apim_service)
        return {
            "name": s.name,
            "location": s.location,
            "sku": s.sku.name if s.sku else None,
            "gateway_url": s.gateway_url,
            "portal_url": s.portal_url,
            "identity_principal_id": s.identity.principal_id if s.identity else None,
            "identity_type": str(s.identity.type) if s.identity else None,
        }

    def get_or_create_backend(self, backend_id: str, foundry_endpoint: str) -> dict[str, Any]:
        """Backend pointing at Foundry with MSI auth handled by policy."""
        from azure.mgmt.apimanagement.models import BackendContract, BackendCredentialsContract

        contract = BackendContract(
            url=foundry_endpoint.rstrip("/") + "/openai",
            protocol="http",
            description=f"AI Watchtower backend for {backend_id}",
        )
        result = self.client.backend.create_or_update(
            settings.resource_group, settings.apim_service, backend_id, contract,
        )
        return {"id": result.id, "name": result.name, "url": result.url}

    def create_api(self, api_id: str, path: str, display_name: str) -> dict[str, Any]:
        from azure.mgmt.apimanagement.models import ApiCreateOrUpdateParameter

        params = ApiCreateOrUpdateParameter(
            display_name=display_name,
            path=path,
            protocols=["https"],
            subscription_required=True,
            api_type="http",
        )
        poller = self.client.api.begin_create_or_update(
            settings.resource_group, settings.apim_service, api_id, params,
        )
        result = poller.result()
        return {"id": result.id, "name": result.name, "path": result.path}

    def create_operation(self, api_id: str, op_id: str, method: str, url_template: str, display_name: str):
        from azure.mgmt.apimanagement.models import OperationContract
        op = OperationContract(
            display_name=display_name, method=method, url_template=url_template,
        )
        self.client.api_operation.create_or_update(
            settings.resource_group, settings.apim_service, api_id, op_id, op,
        )

    def set_api_policy(self, api_id: str, policy_xml: str):
        from azure.mgmt.apimanagement.models import PolicyContract
        pc = PolicyContract(value=policy_xml, format="rawxml")
        self.client.api_policy.create_or_update(
            settings.resource_group, settings.apim_service, api_id, "policy", pc,
        )

    def get_or_create_product(self, product_id: str = "watchtower-governed") -> dict[str, Any]:
        from azure.mgmt.apimanagement.models import ProductContract
        try:
            existing = self.client.product.get(settings.resource_group, settings.apim_service, product_id)
            return {"id": existing.id, "name": existing.name}
        except ResourceNotFoundError:
            pass
        pc = ProductContract(
            display_name="AI Watchtower Governed",
            description="Governed AI endpoints published by AI Watchtower",
            subscription_required=True,
            approval_required=False,
            state="published",
        )
        result = self.client.product.create_or_update(
            settings.resource_group, settings.apim_service, product_id, pc,
        )
        return {"id": result.id, "name": result.name}

    def add_api_to_product(self, product_id: str, api_id: str):
        try:
            self.client.product_api.create_or_update(
                settings.resource_group, settings.apim_service, product_id, api_id,
            )
        except HttpResponseError:
            pass  # already attached

    def create_subscription(self, subscription_id: str, api_id: str, display_name: str) -> dict[str, Any]:
        from azure.mgmt.apimanagement.models import SubscriptionCreateParameters
        scope = f"/apis/{api_id}"
        params = SubscriptionCreateParameters(
            display_name=display_name, scope=scope, state="active",
        )
        result = self.client.subscription.create_or_update(
            settings.resource_group, settings.apim_service, subscription_id, params,
        )
        keys = self.client.subscription.list_secrets(
            settings.resource_group, settings.apim_service, subscription_id,
        )
        return {
            "id": result.id,
            "name": result.name,
            "primary_key": keys.primary_key,
            "secondary_key": keys.secondary_key,
        }

    def set_subscription_state(self, subscription_id: str, state: str):
        """state: active | suspended | cancelled"""
        from azure.mgmt.apimanagement.models import SubscriptionUpdateParameters
        params = SubscriptionUpdateParameters(state=state)
        self.client.subscription.update(
            settings.resource_group, settings.apim_service, subscription_id, "*", params,
        )

    def get_named_value(self, name: str) -> str | None:
        try:
            nv = self.client.named_value.get(settings.resource_group, settings.apim_service, name)
            return nv.value
        except ResourceNotFoundError:
            return None

    def set_named_value(self, name: str, value: str):
        from azure.mgmt.apimanagement.models import NamedValueCreateContract
        params = NamedValueCreateContract(display_name=name, value=value, secret=False)
        poller = self.client.named_value.begin_create_or_update(
            settings.resource_group, settings.apim_service, name, params,
        )
        poller.result()

    def add_to_suspended_csv(self, deployment_name: str):
        current = self.get_named_value("watchtower-suspended-deployments") or ""
        parts = {p.strip() for p in current.split(",") if p.strip() and p.strip() != "__none__"}
        parts.add(deployment_name)
        self.set_named_value("watchtower-suspended-deployments", ",".join(sorted(parts)) or "__none__")

    def remove_from_suspended_csv(self, deployment_name: str):
        current = self.get_named_value("watchtower-suspended-deployments") or ""
        parts = {p.strip() for p in current.split(",") if p.strip() and p.strip() != deployment_name and p.strip() != "__none__"}
        # APIM Named Value rejects empty strings, so seed with a placeholder that no deployment can match.
        self.set_named_value("watchtower-suspended-deployments", ",".join(sorted(parts)) or "__none__")

    def get_api_policy(self, api_id: str) -> str | None:
        try:
            p = self.client.api_policy.get(
                settings.resource_group, settings.apim_service, api_id, "policy",
                format="rawxml",
            )
            return p.value
        except ResourceNotFoundError:
            return None

    def get_subscription_state(self, subscription_id: str) -> str | None:
        try:
            sub = self.client.subscription.get(settings.resource_group, settings.apim_service, subscription_id)
            return sub.state
        except ResourceNotFoundError:
            return None

    def delete_api(self, api_id: str) -> None:
        try:
            self.client.api.delete(settings.resource_group, settings.apim_service, api_id, "*")
        except ResourceNotFoundError:
            pass

    def delete_subscription(self, subscription_id: str) -> None:
        try:
            self.client.subscription.delete(settings.resource_group, settings.apim_service, subscription_id, "*")
        except ResourceNotFoundError:
            pass

    def ensure_tag_and_assign_to_api(self, tag_id: str, tag_display_name: str, api_id: str):
        """Create/update an APIM tag and assign it to the API (for inventory identification)."""
        from azure.mgmt.apimanagement.models import TagCreateUpdateParameters
        tag_id_safe = re.sub(r"[^a-z0-9-]", "-", (tag_id + "-" + tag_display_name).lower())[:80]
        try:
            self.client.tag.create_or_update(
                settings.resource_group, settings.apim_service, tag_id_safe,
                TagCreateUpdateParameters(display_name=f"{tag_id}: {tag_display_name}"),
            )
            self.client.tag.assign_to_api(
                settings.resource_group, settings.apim_service, api_id, tag_id_safe,
            )
        except HttpResponseError:
            pass


# ============================================================================
# Key Vault
# ============================================================================
class KeyVaultClient:
    def __init__(self):
        self.client = SecretClient(vault_url=settings.key_vault_uri, credential=credential())

    def delete_and_purge_secret(self, name: str) -> dict[str, Any]:
        """Soft-delete then purge so nothing remains in the recovery bin."""
        deleted = False
        purged = False
        try:
            poller = self.client.begin_delete_secret(name)
            poller.wait()
            deleted = True
        except ResourceNotFoundError:
            pass
        except Exception:
            pass
        try:
            self.client.purge_deleted_secret(name)
            purged = True
        except ResourceNotFoundError:
            pass
        except Exception:
            pass
        return {"deleted": deleted, "purged": purged}

    def set_secret(self, name: str, value: str, tags: dict[str, str] | None = None) -> dict[str, Any]:
        secret = self.client.set_secret(name, value, tags=tags)
        return {"name": secret.name, "uri": secret.id, "version": secret.properties.version}

    def get_secret(self, name: str) -> str | None:
        try:
            return self.client.get_secret(name).value
        except ResourceNotFoundError:
            return None

    def list_secrets(self) -> list[dict[str, Any]]:
        items = []
        for props in self.client.list_properties_of_secrets():
            items.append({
                "name": props.name, "enabled": props.enabled,
                "tags": props.tags or {}, "created": props.created_on.isoformat() if props.created_on else None,
            })
        return items


# ============================================================================
# Monitor: diagnostics settings + KQL
# ============================================================================
REQUIRED_APIM_LOG_CATEGORIES = ["GatewayLogs"]


class MonitorClient:
    def __init__(self):
        self.client = MonitorManagementClient(credential(), settings.subscription_id)
        self.logs = LogsQueryClient(credential())

    def check_apim_diagnostics(self) -> dict[str, Any]:
        """Verifies APIM diagnostic settings match Watchtower requirements."""
        resource_id = settings.apim_resource_id
        checks: list[dict[str, Any]] = []
        settings_list = list(self.client.diagnostic_settings.list(resource_id))

        # 1) at least one diagnostic settings with GatewayLogs to LA
        gateway_ok = False
        workspace_id: str | None = None
        for ds in settings_list:
            has_gateway = any(
                (log.category == "GatewayLogs" and log.enabled)
                for log in (ds.logs or [])
            )
            if has_gateway and ds.workspace_id:
                gateway_ok = True
                workspace_id = ds.workspace_id
                break

        checks.append({
            "id": "apim-gatewaylogs-to-la",
            "name": "APIM GatewayLogs enabled and shipping to Log Analytics",
            "pass": gateway_ok,
            "remediation": (
                "az monitor diagnostic-settings create "
                f"--name watchtower-apim --resource {resource_id} "
                "--workspace <log-analytics-workspace-id> "
                "--logs '[{\"category\":\"GatewayLogs\",\"enabled\":true}]' "
                "--metrics '[{\"category\":\"AllMetrics\",\"enabled\":true}]'"
            ),
        })

        # 2) APIM /diagnostics/azuremonitor body capture settings
        # This is best-effort - the SDK may not expose the diagnostics entity directly
        body_capture_ok = False
        body_capture_note = "APIM diagnostic entity body capture: manual verification required for v1.0"
        try:
            apim = ApiManagementClient(credential(), settings.subscription_id)
            diag = apim.diagnostic.get(settings.resource_group, settings.apim_service, "azuremonitor")
            sampling_ok = (diag.sampling and diag.sampling.percentage == 100)
            frontend_ok = (
                diag.frontend and diag.frontend.response
                and diag.frontend.response.body and diag.frontend.response.body.bytes >= 8192
            )
            backend_ok = (
                diag.backend and diag.backend.response
                and diag.backend.response.body and diag.backend.response.body.bytes >= 8192
            )
            body_capture_ok = bool(sampling_ok and frontend_ok and backend_ok)
            body_capture_note = f"sampling={sampling_ok}, frontend_body={frontend_ok}, backend_body={backend_ok}"
        except (ResourceNotFoundError, HttpResponseError) as e:
            body_capture_note = f"azuremonitor diagnostic entity not configured: {e}"

        checks.append({
            "id": "apim-body-capture",
            "name": "APIM body capture (100% sampling, 8192B response bodies)",
            "pass": body_capture_ok,
            "detail": body_capture_note,
            "remediation": (
                "Configure the APIM 'azuremonitor' diagnostic entity with sampling.percentage=100, "
                "frontend.response.body.bytes=8192, backend.response.body.bytes=8192. "
                "Without this, content-filter details and jailbreak signals are invisible."
            ),
        })

        overall = all(c["pass"] for c in checks)
        return {
            "overall_pass": overall,
            "workspace_id": workspace_id,
            "checks": checks,
        }

    def query_tokens_by_deployment(self, workspace_id: str, days: int = 30) -> dict[str, dict[str, int]]:
        """Returns real token counts per DeploymentName from the ai-watchtower emit-token-metric.
        Returns {deployment_name: {total: int, prompt: int, completion: int}}.
        Empty dict if no data or query fails."""
        if not workspace_id:
            return {}
        query = f"""
        AppMetrics
        | where TimeGenerated > ago({days}d)
        | where Namespace == 'ai-watchtower'
        | where Name in ('TotalTokens','PromptTokens','CompletionTokens')
        | extend deployment = tostring(Properties.DeploymentName)
        | where isnotempty(deployment)
        | summarize tokens = sum(ValueSum) by deployment, Name
        """
        result: dict[str, dict[str, int]] = {}
        try:
            r = self.logs.query_workspace(workspace_id, query, timespan=timedelta(days=days))
            if r.status != LogsQueryStatus.SUCCESS:
                return {}
            for table in r.tables:
                cols = [c.name for c in table.columns]
                for row in table.rows:
                    d = dict(zip(cols, row))
                    dep = d.get("deployment")
                    metric = d.get("Name", "")
                    val = int(d.get("tokens") or 0)
                    if dep not in result:
                        result[dep] = {"total": 0, "prompt": 0, "completion": 0}
                    if metric == "TotalTokens":
                        result[dep]["total"] = val
                    elif metric == "PromptTokens":
                        result[dep]["prompt"] = val
                    elif metric == "CompletionTokens":
                        result[dep]["completion"] = val
            return result
        except Exception:
            return {}

    def _run_kql(self, workspace_id: str, query: str, hours: int) -> list[dict[str, Any]]:
        if not workspace_id:
            return []
        try:
            r = self.logs.query_workspace(workspace_id, query, timespan=timedelta(hours=hours))
            if r.status != LogsQueryStatus.SUCCESS:
                return []
            rows: list[dict[str, Any]] = []
            for table in r.tables:
                cols = [c.name for c in table.columns]
                for row in table.rows:
                    rows.append(dict(zip(cols, row)))
            return rows
        except HttpResponseError:
            return []
        except Exception:
            return []

    def query_apim_recent_requests(self, workspace_id: str, hours: int = 1, limit: int = 200) -> list[dict[str, Any]]:
        query = f"""
        ApiManagementGatewayLogs
        | where TimeGenerated > ago({hours}h)
        | project TimeGenerated, ApiId, OperationName=OperationId, Method, Url,
                  ResponseCode, BackendResponseCode, TotalTime, ApimSubscriptionId,
                  CallerIpAddress, RequestSize, ResponseSize
        | order by TimeGenerated desc
        | take {limit}
        """
        return self._run_kql(workspace_id, query, hours)

    def query_apim_errors(self, workspace_id: str, hours: int = 24, limit: int = 200) -> list[dict[str, Any]]:
        query = f"""
        ApiManagementGatewayLogs
        | where TimeGenerated > ago({hours}h)
        | where ResponseCode >= 400
        | project TimeGenerated, ApiId, Method, Url, ResponseCode,
                  BackendResponseCode, ApimSubscriptionId, CallerIpAddress,
                  ErrorSource=IsRequestSuccess, LastError=BackendResponseBody
        | order by TimeGenerated desc
        | take {limit}
        """
        return self._run_kql(workspace_id, query, hours)

    def query_apim_rate_limit_hits(self, workspace_id: str, hours: int = 24, limit: int = 200) -> list[dict[str, Any]]:
        query = f"""
        ApiManagementGatewayLogs
        | where TimeGenerated > ago({hours}h)
        | where ResponseCode == 429
        | project TimeGenerated, ApiId, Method, Url, ApimSubscriptionId, CallerIpAddress
        | order by TimeGenerated desc
        | take {limit}
        """
        return self._run_kql(workspace_id, query, hours)

    def query_apim_summary(self, workspace_id: str, hours: int = 24) -> dict[str, Any]:
        query = f"""
        ApiManagementGatewayLogs
        | where TimeGenerated > ago({hours}h)
        | summarize
            total = count(),
            errors = countif(ResponseCode >= 400),
            rate_limits = countif(ResponseCode == 429),
            server_errors = countif(ResponseCode >= 500),
            unique_apis = dcount(ApiId),
            unique_callers = dcount(CallerIpAddress),
            avg_latency_ms = avg(TotalTime),
            p95_latency_ms = percentile(TotalTime, 95)
        """
        rows = self._run_kql(workspace_id, query, hours)
        return rows[0] if rows else {}

    def query_apim_traffic_timeline(self, workspace_id: str, hours: int = 24, bin_minutes: int = 15) -> list[dict[str, Any]]:
        query = f"""
        ApiManagementGatewayLogs
        | where TimeGenerated > ago({hours}h)
        | summarize
            requests = count(),
            errors = countif(ResponseCode >= 400)
            by ApiId, bin(TimeGenerated, {bin_minutes}m)
        | order by TimeGenerated asc
        """
        return self._run_kql(workspace_id, query, hours)

    def query_foundry_diagnostics(self, workspace_id: str, hours: int = 24, limit: int = 100) -> list[dict[str, Any]]:
        """Query Cognitive Services diagnostic logs (RequestResponse / Audit) if diagnostic settings
        are enabled on the Foundry account. Empty if not configured."""
        query = f"""
        AzureDiagnostics
        | where TimeGenerated > ago({hours}h)
        | where ResourceProvider == 'MICROSOFT.COGNITIVESERVICES'
        | project TimeGenerated, Resource, OperationName, ResultType, ResultSignature,
                  DurationMs, CallerIpAddress, ResultDescription, Category
        | order by TimeGenerated desc
        | take {limit}
        """
        return self._run_kql(workspace_id, query, hours)

    def query_tokens_by_day(self, workspace_id: str, deployment_name: str, days: int = 30) -> list[dict[str, Any]]:
        """Per-day token counts for a single deployment. Empty list if no data or query fails."""
        if not workspace_id:
            return []
        query = f"""
        AppMetrics
        | where TimeGenerated > ago({days}d)
        | where Namespace == 'ai-watchtower'
        | where Name in ('TotalTokens','PromptTokens','CompletionTokens')
        | extend deployment = tostring(Properties.DeploymentName)
        | where deployment == '{deployment_name}'
        | summarize tokens = sum(ValueSum) by bin(TimeGenerated, 1d), Name
        | order by TimeGenerated asc
        """
        by_day: dict[str, dict[str, int]] = {}
        try:
            r = self.logs.query_workspace(workspace_id, query, timespan=timedelta(days=days))
            if r.status != LogsQueryStatus.SUCCESS:
                return []
            for table in r.tables:
                cols = [c.name for c in table.columns]
                for row in table.rows:
                    d = dict(zip(cols, row))
                    ts = d.get("TimeGenerated")
                    day = (ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]) if ts else "unknown"
                    metric = d.get("Name", "")
                    val = int(d.get("tokens") or 0)
                    entry = by_day.setdefault(day, {"total": 0, "prompt": 0, "completion": 0})
                    if metric == "TotalTokens":
                        entry["total"] = val
                    elif metric == "PromptTokens":
                        entry["prompt"] = val
                    elif metric == "CompletionTokens":
                        entry["completion"] = val
        except Exception:
            return []
        return [{"day": k, **v} for k, v in sorted(by_day.items())]

    def query_jailbreak_attempts(self, workspace_id: str, hours: int = 24) -> list[dict[str, Any]]:
        """Jailbreak + indirect attack detections from APIM gateway logs.
        Requires body capture (frontend.response.body.bytes >= 8192)."""
        if not workspace_id:
            return []
        query = f"""
        ApiManagementGatewayLogs
        | where TimeGenerated > ago({hours}h)
        | where ResponseCode == 400
        | extend body = parse_json(BackendResponseBody)
        | extend filt = body.error.innererror.content_filter_result
        | extend jailbreak_detected = tobool(filt.jailbreak.detected)
        | extend indirect_attack_detected = tobool(filt.indirect_attack.detected)
        | where jailbreak_detected == true or indirect_attack_detected == true
        | project TimeGenerated, ApimSubscriptionId, Url, CallerIpAddress,
                  jailbreak_detected, indirect_attack_detected
        | order by TimeGenerated desc
        | take 200
        """
        try:
            r = self.logs.query_workspace(workspace_id, query, timespan=timedelta(hours=hours))
            if r.status != LogsQueryStatus.SUCCESS:
                return []
            rows = []
            for table in r.tables:
                cols = [c.name for c in table.columns]
                for row in table.rows:
                    rows.append(dict(zip(cols, row)))
            return rows
        except HttpResponseError:
            return []

    def query_config_drift(self, hours: int = 168, deployment_name: str | None = None) -> list[dict[str, Any]]:
        """Configuration change events for the target Foundry account, from Azure Activity Log.
        Captures deployment writes (SKU changes, capacity changes, RAI policy changes) and
        account-level modifications (network, local auth, etc.). Last 7 days by default.

        If deployment_name given, filters to events affecting that specific deployment
        (its own /deployments/{name} resource writes OR account-level writes that impact all)."""
        import httpx
        from datetime import datetime, timezone
        start = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Filter to CognitiveServices resource-write events on our Foundry account
        target_prefix = settings.foundry_resource_id.lower()
        try:
            token = credential().get_token("https://management.azure.com/.default").token
        except Exception:
            return []
        # Activity Log via ARM management activity log endpoint
        filter_clause = (
            f"eventTimestamp ge '{start}' "
            "and resourceProvider eq 'Microsoft.CognitiveServices'"
        )
        url = (
            f"https://management.azure.com/subscriptions/{settings.subscription_id}"
            f"/providers/Microsoft.Insights/eventtypes/management/values"
            f"?api-version=2015-04-01&$filter={filter_clause}"
        )
        try:
            with httpx.Client(timeout=60) as c:
                r = c.get(url, headers={"Authorization": f"Bearer {token}"})
            if r.status_code >= 400:
                return []
            events = r.json().get("value", [])
        except Exception:
            return []

        rows = []
        dep_suffix = None
        if deployment_name:
            dep_suffix = f"/deployments/{deployment_name.lower()}"
        for e in events:
            rid = (e.get("resourceId") or "").lower()
            if not rid.startswith(target_prefix):
                continue
            op = e.get("operationName", {}) or {}
            op_name = op.get("value") or op.get("localizedValue") or ""
            if not any(op_name.endswith(x) for x in (
                "/write", "/action", "/delete",
            )):
                continue

            # If filtering per-deployment: keep events on that specific deployment resource
            # OR account-level events that impact all deployments (network / local-auth / etc.)
            if dep_suffix:
                is_this_deployment = dep_suffix in rid
                is_account_level = rid.endswith(target_prefix)
                if not (is_this_deployment or is_account_level):
                    continue

            resource_type = rid.split("/providers/microsoft.cognitiveservices/")[-1] if "providers/microsoft.cognitiveservices/" in rid else rid
            caller = e.get("caller") or ""
            status = (e.get("status") or {}).get("value") or ""
            scope = "deployment" if (dep_suffix and dep_suffix in rid) else "account"
            rows.append({
                "timestamp": e.get("eventTimestamp"),
                "operation": op_name,
                "resource": resource_type,
                "caller": caller,
                "status": status,
                "scope": scope,
                "correlation_id": e.get("correlationId"),
            })
        rows.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
        return rows[:200]

    def query_blocked_content(self, workspace_id: str, hours: int = 1) -> list[dict[str, Any]]:
        """Blocked content by category over the last N hours. Requires body capture."""
        if not workspace_id:
            return []
        query = f"""
        ApiManagementGatewayLogs
        | where TimeGenerated > ago({hours}h)
        | where ResponseCode == 400
        | extend body = parse_json(BackendResponseBody)
        | where tostring(body.error.code) == 'content_filter'
        | extend filt = body.error.innererror.content_filter_result
        | mv-expand cat = bag_keys(filt)
        | extend category = tostring(cat),
                 filtered = tobool(filt[tostring(cat)].filtered),
                 severity = tostring(filt[tostring(cat)].severity)
        | where filtered == true
        | summarize blocks = count() by ApimSubscriptionId, category, severity, bin(TimeGenerated, 5m)
        | order by TimeGenerated desc
        | take 200
        """
        try:
            result = self.logs.query_workspace(workspace_id, query, timespan=timedelta(hours=hours))
            if result.status != LogsQueryStatus.SUCCESS:
                return []
            rows = []
            for table in result.tables:
                col_names = [c.name for c in table.columns]
                for row in table.rows:
                    rows.append(dict(zip(col_names, row)))
            return rows
        except HttpResponseError:
            return []


# ============================================================================
# Cost Management: monthly per-resource cost
# ============================================================================
class CostClient:
    """Query Cost Management for monthly Foundry costs. Uses ARM REST directly."""

    def monthly_by_meter(self, year: int, month: int) -> list[dict[str, Any]]:
        """Returns cost rows for the Foundry account, one per meter, for the given month."""
        import httpx
        from datetime import date

        first = date(year, month, 1)
        if month == 12:
            last = date(year + 1, 1, 1)
        else:
            last = date(year, month + 1, 1)

        token = credential().get_token("https://management.azure.com/.default").token
        url = (
            f"https://management.azure.com/subscriptions/{settings.subscription_id}"
            f"/providers/Microsoft.CostManagement/query?api-version=2023-11-01"
        )
        body = {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": {
                "from": first.isoformat() + "T00:00:00Z",
                "to": (last.isoformat() + "T00:00:00Z"),
            },
            "dataset": {
                "granularity": "None",
                "aggregation": {
                    "totalCost": {"name": "PreTaxCost", "function": "Sum"},
                },
                "grouping": [
                    {"type": "Dimension", "name": "ResourceId"},
                    {"type": "Dimension", "name": "MeterCategory"},
                    {"type": "Dimension", "name": "MeterSubCategory"},
                    {"type": "Dimension", "name": "Meter"},
                ],
                "filter": {
                    "Dimensions": {
                        "Name": "ResourceId",
                        "Operator": "In",
                        "Values": [settings.foundry_resource_id.lower()],
                    }
                },
            },
        }
        try:
            with httpx.Client(timeout=60) as client:
                r = client.post(url, headers={"Authorization": f"Bearer {token}"}, json=body)
            if r.status_code >= 400:
                return []
            data = r.json()
            cols = [c["name"] for c in data.get("properties", {}).get("columns", [])]
            rows = data.get("properties", {}).get("rows", [])
            return [dict(zip(cols, row)) for row in rows]
        except Exception:
            return []


# ============================================================================
# Authorization: grant APIM MI Cognitive Services OpenAI User on Foundry
# ============================================================================
COGNITIVE_SERVICES_OPENAI_USER_ROLE_ID = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"


class AuthClient:
    def __init__(self):
        self.client = AuthorizationManagementClient(credential(), settings.subscription_id)

    def ensure_apim_can_call_foundry(self, apim_principal_id: str) -> dict[str, Any]:
        """Assisted mode: grant APIM MI the Cognitive Services OpenAI User role on the Foundry account."""
        from azure.mgmt.authorization.models import RoleAssignmentCreateParameters
        role_definition_id = (
            f"/subscriptions/{settings.subscription_id}"
            f"/providers/Microsoft.Authorization/roleDefinitions/{COGNITIVE_SERVICES_OPENAI_USER_ROLE_ID}"
        )
        scope = settings.foundry_resource_id
        # deterministic assignment id so re-runs are idempotent
        seed = f"{scope}-{apim_principal_id}-{COGNITIVE_SERVICES_OPENAI_USER_ROLE_ID}"
        digest = hashlib.sha1(seed.encode()).digest()
        # format as GUID
        assignment_id = (
            f"{digest[0:4].hex()}-{digest[4:6].hex()}-{digest[6:8].hex()}-"
            f"{digest[8:10].hex()}-{digest[10:16].hex()}"
        )
        try:
            existing = self.client.role_assignments.get(scope, assignment_id)
            return {"status": "exists", "assignment_id": existing.id}
        except (ResourceNotFoundError, HttpResponseError):
            pass
        try:
            params = RoleAssignmentCreateParameters(
                role_definition_id=role_definition_id,
                principal_id=apim_principal_id,
                principal_type="ServicePrincipal",
            )
            result = self.client.role_assignments.create(scope, assignment_id, params)
            return {"status": "created", "assignment_id": result.id}
        except HttpResponseError as e:
            return {"status": "error", "error": str(e)}
