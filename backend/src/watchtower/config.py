from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """AI Watchtower configuration. All values come from environment variables
    prefixed with WATCHTOWER_. Nothing is hardcoded to a specific tenant, account,
    or region so this app is portable across environments.

    Set via .env file (dev) or App Service application settings (prod).
    See .env.example for the full list.
    """
    model_config = SettingsConfigDict(env_prefix="WATCHTOWER_", case_sensitive=False)

    # Azure tenancy - required
    subscription_id: str = ""
    resource_group: str = ""
    location: str = ""

    # Existing Azure resources the platform manages (must exist before deploy)
    foundry_account: str = ""
    apim_service: str = ""
    key_vault: str = ""
    content_safety_account: str = ""

    # User-assigned managed identity - populated by Bicep at deploy time
    managed_identity_client_id: str | None = None

    # Persistence
    db_path: str = "/home/data/watchtower.db"       # SQLite fallback for local dev
    database_url: str | None = None                 # Postgres in prod

    cors_origins: list[str] = ["*"]

    @property
    def foundry_resource_id(self) -> str:
        return (
            f"/subscriptions/{self.subscription_id}"
            f"/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.CognitiveServices/accounts/{self.foundry_account}"
        )

    @property
    def apim_resource_id(self) -> str:
        return (
            f"/subscriptions/{self.subscription_id}"
            f"/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.ApiManagement/service/{self.apim_service}"
        )

    @property
    def key_vault_uri(self) -> str:
        return f"https://{self.key_vault}.vault.azure.net"


@lru_cache
def get_settings() -> Settings:
    return Settings()
