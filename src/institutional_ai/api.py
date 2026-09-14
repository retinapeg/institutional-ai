"""Minimal HTTP surface; orchestration remains in the engine."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from .engine import InstitutionalEngine, InstitutionalError
from .models import OrganisationEdge, ProjectState
from .store import ProjectStore, StoreError


class CreateProjectRequest(BaseModel):
    mission: str = Field(min_length=1, max_length=2_000)

    @field_validator("mission")
    @classmethod
    def mission_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("mission must not be blank")
        return value


DEFAULT_MISSION = (
    "Analyse a toy urban fleet optimisation problem and propose an efficient modelling strategy."
)


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    root = Path(data_dir or os.environ.get("INSTITUTIONAL_AI_DATA_DIR", ".institutional-data"))
    store = ProjectStore(root)
    engine = InstitutionalEngine(store)
    app = FastAPI(title="Institutional AI", version="0.1.0")
    app.state.store = store
    app.state.engine = engine
    # ponytail: one process-wide lock is enough for the local MVP; use per-project locks for throughput.
    app.state.write_lock = Lock()
    static = Path(__file__).parent / "static" / "index.html"

    @app.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(static)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "provider": engine.provider.name, "mode": "deterministic"}

    @app.post("/api/projects", response_model=ProjectState, status_code=201)
    def create_project(request: CreateProjectRequest) -> ProjectState:
        with app.state.write_lock:
            return engine.create_project(request.mission)

    @app.post("/api/projects/{project_id}/run", response_model=ProjectState)
    def run_project(project_id: str) -> ProjectState:
        try:
            with app.state.write_lock:
                return engine.run(project_id)
        except StoreError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InstitutionalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/projects/{project_id}/recover", response_model=ProjectState)
    def recover_project(project_id: str) -> ProjectState:
        try:
            with app.state.write_lock:
                return engine.recover(project_id)
        except StoreError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InstitutionalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/projects/latest", response_model=ProjectState)
    def latest_project() -> ProjectState:
        project = store.latest()
        if project is None:
            raise HTTPException(status_code=404, detail="no project has been created")
        return project

    @app.get("/api/projects/{project_id}", response_model=ProjectState)
    def get_project(project_id: str) -> ProjectState:
        try:
            return store.load(project_id)
        except StoreError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}/organisation", response_model=list[OrganisationEdge])
    def get_organisation(
        project_id: str,
        at_event: int | None = Query(default=None, ge=1),
    ) -> list[OrganisationEdge]:
        try:
            project = store.load(project_id)
        except StoreError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return engine.organisation_at(project, at_event)

    return app
