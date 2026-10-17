"""Small, atomic JSON persistence for local extraction projects.

The app currently has no database or account system. Project records therefore
live beside the existing per-document files in ``data/`` and refer to the
existing document IDs instead of copying uploads or indexes.
"""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from ..config import EXTRACTION_PROJECTS_DIR


class ExtractionProjectStore:
    def __init__(self, root: Path = EXTRACTION_PROJECTS_DIR) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._projects: dict[str, dict[str, Any]] = {}
        self._load()

    def _path(self, project_id: str) -> Path:
        return self.root / f"{project_id}.json"

    def _write(self, project: dict[str, Any]) -> None:
        path = self._path(project["id"])
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(project, ensure_ascii=False, default=str))
        tmp.replace(path)

    def _load(self) -> None:
        for path in self.root.glob("*.json"):
            try:
                project = json.loads(path.read_text())
                if not isinstance(project, dict) or not project.get("id"):
                    continue
                job = project.setdefault("job", {})
                if job.get("status") in {"queued", "running"}:
                    job.update({
                        "status": "error",
                        "stage": "Interrupted",
                        "error": "The backend restarted during this job. Start extraction again to resume.",
                        "finished_at": time.time(),
                    })
                    project["status"] = "error"
                    self._write(project)
                project.setdefault("documents", [])
                project.setdefault("schema", {"fields": []})
                project.setdefault("schema_version", 0)
                project.setdefault("observations", [])
                self._projects[project["id"]] = project
            except Exception as exc:
                print(f"[extraction] skipping project {path.name}: {exc}")

    def create(self, name: str, objective: str) -> dict[str, Any]:
        now = time.time()
        project = {
            "id": uuid.uuid4().hex,
            "name": name.strip() or "Untitled extraction",
            "objective": objective.strip(),
            "observation_unit": "",
            "documents": [],
            "schema": {"fields": []},
            "schema_version": 0,
            "schema_versions": [],
            "observations": [],
            "cloud_consent_at": None,
            "status": "draft",
            "job": {"id": None, "status": "idle", "stage": "Not started", "progress": 0.0,
                    "error": None, "started_at": None, "finished_at": None},
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._write(project)
            self._projects[project["id"]] = project
            return copy.deepcopy(project)

    def get(self, project_id: str) -> dict[str, Any] | None:
        with self._lock:
            project = self._projects.get(project_id)
            return copy.deepcopy(project) if project else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            values = list(self._projects.values())
            values.sort(key=lambda p: p.get("updated_at", 0), reverse=True)
            return copy.deepcopy(values)

    def update(self, project_id: str, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any] | None:
        with self._lock:
            existing = self._projects.get(project_id)
            if existing is None:
                return None
            project = copy.deepcopy(existing)
            mutate(project)
            project["updated_at"] = time.time()
            self._write(project)
            self._projects[project_id] = project
            return copy.deepcopy(project)

    def delete(self, project_id: str) -> bool:
        with self._lock:
            project = self._projects.pop(project_id, None)
            if project is None:
                return False
            self._path(project_id).unlink(missing_ok=True)
            return True


projects = ExtractionProjectStore()
