"""Small local persistence layer with attributable worker workspaces."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .models import (
    Artifact,
    ArtifactStatus,
    AuditEvent,
    MemoryRecord,
    MemoryScope,
    ProjectState,
    utc_now,
)


class StoreError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ProjectStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project_id: str) -> Path:
        if not project_id or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for char in project_id
        ):
            raise StoreError("invalid project id")
        return self.root / project_id

    def create(self, project: ProjectState) -> None:
        directory = self.project_dir(project.id)
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "workspaces").mkdir()
        self.save(project)

    def save(self, project: ProjectState) -> None:
        project.updated_at = utc_now()
        directory = self.project_dir(project.id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "state.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            project.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)

    def load(self, project_id: str) -> ProjectState:
        path = self.project_dir(project_id) / "state.json"
        if not path.is_file():
            raise StoreError(f"unknown project {project_id}")
        return ProjectState.model_validate_json(path.read_text(encoding="utf-8"))

    def latest(self) -> ProjectState | None:
        snapshots = sorted(
            self.root.glob("*/state.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return self.load(snapshots[0].parent.name) if snapshots else None

    def load_organisation_memory(self) -> list[MemoryRecord]:
        path = self.root / "organisation-memory.jsonl"
        if not path.is_file():
            return []
        return [
            MemoryRecord.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def append_organisation_memory(self, memory: MemoryRecord) -> None:
        if memory.scope != MemoryScope.ORGANISATION:
            raise StoreError("only organisation memory belongs in the shared memory log")
        path = self.root / "organisation-memory.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(memory.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def record_event(
        self,
        project: ProjectState,
        event_type: str,
        actor_id: str,
        entity_id: str,
        details: dict[str, object] | None = None,
    ) -> AuditEvent:
        previous_hash = project.audit_events[-1].hash if project.audit_events else None
        sequence = len(project.audit_events) + 1
        timestamp = utc_now()
        event_details = details or {}
        body = {
            "sequence": sequence,
            "event_type": event_type,
            "actor_id": actor_id,
            "entity_id": entity_id,
            "details": event_details,
            "timestamp": timestamp,
            "previous_hash": previous_hash,
        }
        event = AuditEvent(
            sequence=sequence,
            event_type=event_type,
            actor_id=actor_id,
            entity_id=entity_id,
            details=event_details,
            timestamp=timestamp,
            previous_hash=previous_hash,
            hash=sha256_text(canonical_json(body)),
        )
        event_path = self.project_dir(project.id) / "events.jsonl"
        with event_path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        project.audit_events.append(event)
        return event

    def ensure_workspace(self, project: ProjectState, worker_id: str) -> Path:
        worker = project.workers.get(worker_id)
        if worker is None:
            raise StoreError(f"unknown worker {worker_id}")
        workspace = self.project_dir(project.id) / worker.workspace
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace.resolve()

    def write_artifact(
        self,
        project: ProjectState,
        worker_id: str,
        task_id: str,
        relative_path: str,
        content: str,
        *,
        media_type: str = "text/markdown",
        dependencies: list[str] | None = None,
        reviewers: list[str] | None = None,
        status: ArtifactStatus = ArtifactStatus.COMMITTED,
    ) -> Artifact:
        if task_id not in project.tasks:
            raise StoreError(f"unknown task {task_id}")
        task = project.tasks[task_id]
        if task.owner_worker_id != worker_id:
            raise StoreError("worker cannot create an artifact for another worker's task")
        workspace = self.ensure_workspace(project, worker_id)
        target = (workspace / relative_path).resolve()
        if target == workspace or workspace not in target.parents:
            raise StoreError("artifact path escapes the worker workspace")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        artifact = Artifact(
            project_id=project.id,
            creator_worker_id=worker_id,
            source_task_id=task_id,
            relative_path=str(target.relative_to(self.project_dir(project.id))),
            media_type=media_type,
            dependencies=dependencies or [],
            reviewers=reviewers or [],
            status=status,
            sha256=sha256_text(content),
        )
        project.artifacts[artifact.id] = artifact
        self.record_event(
            project,
            "artifact.committed",
            worker_id,
            artifact.id,
            {"path": artifact.relative_path, "sha256": artifact.sha256},
        )
        return artifact

    def retrieve_memory(
        self,
        project: ProjectState,
        worker_id: str,
        tags: set[str],
        *,
        limit: int = 8,
    ) -> list[MemoryRecord]:
        eligible = []
        for memory in project.memories.values():
            visible = (
                memory.scope == MemoryScope.ORGANISATION
                or (memory.scope == MemoryScope.PROJECT and memory.owner_id == project.id)
                or (memory.scope == MemoryScope.WORKER and memory.owner_id == worker_id)
            )
            if visible and (not tags or tags.intersection(memory.tags)):
                eligible.append(memory)
        return sorted(eligible, key=lambda item: item.created_at, reverse=True)[:limit]

    def write_manifest(self, project: ProjectState) -> Path:
        state_path = self.project_dir(project.id) / "state.json"
        event_path = self.project_dir(project.id) / "events.jsonl"
        manifest = {
            "project_id": project.id,
            "status": project.status,
            "state_sha256": sha256_text(state_path.read_text(encoding="utf-8")),
            "event_log_sha256": sha256_text(event_path.read_text(encoding="utf-8")),
            "event_count": len(project.audit_events),
            "worker_count": len(project.workers),
            "report_count": len(project.reports),
            "artifact_count": len(project.artifacts),
            "created_at": utc_now(),
        }
        target = self.project_dir(project.id) / "manifest.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
        os.replace(temporary, target)
        return target
