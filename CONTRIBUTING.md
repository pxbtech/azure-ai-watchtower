# Contributing to AI Watchtower

Thanks for your interest. This is a small project and PRs are welcome. Please read below before opening one.

## Before you start work

- **Open an issue first for anything non-trivial.** A short issue thread saves both of us time if the change conflicts with something in flight or with the project's direction.
- **Small PRs merge faster.** If your change touches more than ~500 lines across multiple areas, split it.
- **Backwards compatibility is nice but not sacred.** The project is pre-1.0. Breaking changes are fine when justified; call them out in the PR description.

## Development setup

Full setup in [`README.md`](README.md#local-development). Short version:

```bash
# Backend
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn watchtower.main:app --reload --port 8000

# Frontend
cd frontend && npm install && npm run dev
```

You need Azure credentials for anything that talks to real resources. `az login` is enough for local dev; `DefaultAzureCredential` will pick it up.

## Style

- **Python**: PEP 8, type hints on public functions. Ruff-friendly (no strict config yet). No comments explaining what the code does; comments only for the *why* when it is non-obvious.
- **TypeScript**: strict mode is on. Prefer functional components + TanStack Query hooks. Keep pages thin, push logic into hooks / clients under `src/lib/`.
- **CSS**: use the Argon variables from `overrides.css`. **Do not add bespoke colors.** Thin borders, no pill backgrounds on badges.
- **No em dashes** in UI copy, comments, or docs. Use `-` or `,` instead. (This is a project convention, not a technical constraint.)
- **No mock or synthetic data.** If a control-plane call fails, surface the failure honestly. Empty states should say "no data yet", not fake numbers.

## Testing

There is no test suite yet (contributions welcome). At minimum, run a smoke test before opening a PR:

1. `uvicorn` starts without traceback.
2. `npm run build` completes.
3. `az bicep build --file infra/main.bicep` completes with no errors or warnings.

## Commit messages

Conventional-style prefixes are appreciated but not required: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`. Squash-merge is the default.

## Areas that need help

- Test coverage (pytest for backend, Playwright for a golden-path UI test)
- More APIM policy fragments (semantic caching, PII redaction, jailbreak detection via Content Safety Prompt Shields)
- Additional Foundry model families in the hardcoded price table (fallback for Retail API gaps)
- Alternative persistence backends (currently: SQLite for dev, Postgres for prod)
