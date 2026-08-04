from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from .config import get_settings
from .db import init_db
from .routers import (
    discovery, foundries, deployments, diagnostics, security, audit,
    details, billing, intake, projects, monitoring,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    from .workers.budget_enforcement import start as start_budget, stop as stop_budget
    start_budget()
    try:
        yield
    finally:
        await stop_budget()


app = FastAPI(
    title="AI Watchtower",
    description="Governance and metering control plane for Azure AI Foundry deployments.",
    version="1.3.0",
    lifespan=lifespan,
)

s = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=s.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Order matters: put more-specific routers first so /api/deployments/{name}/details
# doesn't get swallowed by /api/deployments/{name}
app.include_router(intake.router)
app.include_router(projects.router)
app.include_router(details.router)
app.include_router(deployments.router)
app.include_router(foundries.router)
app.include_router(discovery.router)
app.include_router(diagnostics.router)
app.include_router(security.router)
app.include_router(billing.router)
app.include_router(monitoring.router)
app.include_router(audit.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": "AI Watchtower", "version": "1.3.0"}


@app.get("/api/config")
async def public_config():
    return {
        "subscription_id": s.subscription_id,
        "resource_group": s.resource_group,
        "location": s.location,
        "foundry_account": s.foundry_account,
        "apim_service": s.apim_service,
        "key_vault": s.key_vault,
    }


_frontend_dist = Path(__file__).parent / "static"
if _frontend_dist.exists():
    _assets = _frontend_dist / "assets"
    if _assets.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")
    _argon = _frontend_dist / "argon"
    if _argon.exists():
        app.mount("/argon", StaticFiles(directory=str(_argon)), name="argon")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        index = _frontend_dist / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return JSONResponse({"detail": "Frontend not built."}, status_code=404)
else:
    @app.get("/")
    async def root():
        return {"app": "AI Watchtower", "docs": "/docs"}
