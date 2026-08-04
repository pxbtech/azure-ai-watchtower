from datetime import datetime, timezone
from sqlalchemy import String, DateTime, JSON, Integer, Float, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Bump when the schema changes; startup drops and recreates the DB.
SCHEMA_VERSION = 3


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class DeploymentRecord(Base):
    """AI Watchtower record of a governed Foundry deployment published through APIM."""
    __tablename__ = "deployment_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deployment_name: Mapped[str] = mapped_column(String(200), index=True, unique=True)
    foundry_account: Mapped[str] = mapped_column(String(100))
    project_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_name: Mapped[str] = mapped_column(String(100))
    model_version: Mapped[str] = mapped_column(String(50))
    sku_name: Mapped[str] = mapped_column(String(50))
    capacity: Mapped[int] = mapped_column(Integer)
    rai_policy_name: Mapped[str] = mapped_column(String(100))

    # Ownership / chargeback / intake metadata
    app_name: Mapped[str] = mapped_column(String(120))
    app_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    app_team: Mapped[str | None] = mapped_column(String(120), nullable=True)
    business_unit: Mapped[str | None] = mapped_column(String(120), nullable=True)
    environment: Mapped[str | None] = mapped_column(String(30), nullable=True)  # dev|test|prod
    cost_center: Mapped[str | None] = mapped_column(String(60), nullable=True)
    use_case_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Governance settings applied at publish time
    tpm_limit: Mapped[int] = mapped_column(Integer, default=10000)           # tokens per minute per consumer
    throttling_rpm: Mapped[int] = mapped_column(Integer, default=60)          # requests per minute per consumer
    monthly_budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold_pct: Mapped[int] = mapped_column(Integer, default=95)

    # APIM + KV wiring
    apim_api_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    apim_subscription_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    kv_secret_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    kv_secret_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)

    state: Mapped[str] = mapped_column(String(30), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SuspensionRecord(Base):
    __tablename__ = "suspension_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deployment_name: Mapped[str] = mapped_column(String(200), index=True)
    action: Mapped[str] = mapped_column(String(20))
    layers_applied: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100))
    target_type: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[str] = mapped_column(String(500))
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class SchemaMarker(Base):
    __tablename__ = "schema_marker"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
