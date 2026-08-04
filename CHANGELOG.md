# Changelog

All notable changes to AI Watchtower will be documented here. Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [Unreleased]

## [0.1.0] - Initial public release

### Added

- One-shot intake: Foundry project + deployment + APIM API + subscription + Key Vault secret in one atomic form submission.
- APIM policy template with `azure-openai-token-limit`, `rate-limit-by-key`, `authentication-managed-identity`, `azure-openai-emit-token-metric`, and metadata header injection.
- Budget enforcement worker: 60s polling loop, real Log Analytics token metrics, pricing via Azure Retail Prices API, three-layer auto-suspend at threshold %.
- Per-deployment MTD cost + burn %, computed from real usage.
- OWASP LLM Top 10 compliance grading (A/B/C/F) per model.
- Config drift detection via Azure Activity Log, per-endpoint filtering.
- Delete cascade: APIM + KV soft-delete purge + DB record, typed confirmation required.
- PDF export per endpoint via reportlab.
- Bicep IaC: user-assigned MI, cross-scope RBAC, App Service Plan, Postgres Flexible Server.
- React 18 SPA on Argon Dashboard theme.

### Notes

- Requires APIM v2+ for token-limit policy; degrades to RPM-only on v1.
- Budget enforcement latency ~90-150s (LA ingestion + worker interval); not for use as sole runaway-loop safeguard.
