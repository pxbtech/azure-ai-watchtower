from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException
from ..azure_clients import FoundryClient

router = APIRouter(prefix="/api/foundries", tags=["foundries"])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=63, pattern=r"^[a-zA-Z0-9-]+$")


@router.get("/current")
async def get_current():
    try:
        return FoundryClient().get_account()
    except Exception as e:
        raise HTTPException(500, f"Foundry account read failed: {e}")


@router.get("/current/projects")
async def list_projects():
    try:
        return FoundryClient().list_projects()
    except Exception as e:
        raise HTTPException(500, f"Project list failed: {e}")


@router.post("/current/projects", status_code=201)
async def create_project(payload: ProjectCreate):
    try:
        return FoundryClient().create_project(payload.name)
    except Exception as e:
        raise HTTPException(500, f"Project create failed: {e}")


@router.get("/current/models")
async def list_available_models():
    try:
        return FoundryClient().list_available_models()
    except Exception as e:
        raise HTTPException(500, f"Model list failed: {e}")


@router.get("/current/deployments")
async def list_deployments():
    try:
        return FoundryClient().list_deployments()
    except Exception as e:
        raise HTTPException(500, f"Deployment list failed: {e}")
