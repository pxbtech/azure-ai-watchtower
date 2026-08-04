# Security policy

## Reporting a vulnerability

If you find a security issue in AI Watchtower, **do not open a public GitHub issue**. Instead, please email the maintainers (address in the repo's About section, or open a private security advisory via GitHub).

Include:

- A description of the issue
- Steps to reproduce (proof-of-concept if possible)
- The affected version / commit
- Your assessment of impact (data exposure, privilege escalation, denial of service, etc.)

We aim to acknowledge within 3 business days and issue a fix or mitigation within 30 days for high-severity issues.

## Scope

In scope:

- The AI Watchtower app itself (backend, frontend, workers)
- The Bicep infrastructure template
- The APIM policy templates shipped in this repo

Out of scope:

- Vulnerabilities in Azure services themselves (report to Microsoft via [MSRC](https://msrc.microsoft.com/))
- Vulnerabilities in third-party dependencies (report upstream; we will pull in patched versions once released)
- Misconfigurations of your own Azure environment (e.g. weak Key Vault policies, exposed APIM endpoints, wrong RBAC grants). AI Watchtower cannot enforce hardening that you have not applied to the underlying resources.

## Security posture summary

- Managed identity only. No client secrets, no keys in App Service settings, no keys in source.
- Key Vault RBAC (not access policies). Delete cascade purges secrets from soft-delete.
- HTTPS only, TLS 1.2 min, FTPS disabled.
- APIM subscription keys are the only client credential; auto-provisioned into KV at intake time, never displayed in UI.
- Bicep grants User Access Administrator **scoped to the Foundry account only**, not to the RG or subscription.
- OWASP LLM Top 10 grading is defensive posture measurement, not a substitute for penetration testing.

## Known limitations

- The APIM `azure-openai-token-limit` policy requires APIM v2 or later. On v1 tiers, token limiting silently degrades to RPM-only.
- Budget enforcement latency is bounded by the Log Analytics ingestion lag (typically 30-90 seconds) plus the 60-second worker interval. Do not use it as the sole safeguard against a runaway loop.
- The Azure Retail Prices API can lag Foundry model launches by days to weeks. Unknown models fall back to a hardcoded price table; endpoints for models with no price data show cost as "unknown" rather than $0.
