from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .git_projects import resolve_project
from .history import HistoryIndex
from .projects import ProjectManager
from .reconcile import scan_external_transcripts, summarize_transcript


def _normalized_path(value: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(value))).rstrip("\\/")


def _contains(root: str, candidate: str) -> bool:
    try:
        normalized_root = _normalized_path(root)
        return os.path.commonpath((normalized_root, _normalized_path(candidate))) == normalized_root
    except ValueError:
        return False


@dataclass
class BackfillJob:
    id: str
    project_id: str
    project_name: str
    status: str = "queued"
    phase: str = "queued"
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    scanned: int = 0
    total: int = 0
    processed: int = 0
    discovered: int = 0
    indexed: int = 0
    indexed_messages: int = 0
    unchanged: int = 0
    ambiguous: int = 0
    unreadable: int = 0
    error: str | None = None
    cancel_requested: bool = False

    def payload(self) -> dict[str, Any]:
        return asdict(self)


class HistoryBackfillManager:
    """Runs explicit, cancellable, project-scoped native transcript scans."""

    def __init__(
        self, history: HistoryIndex, projects: ProjectManager, *, home: Path | None = None
    ) -> None:
        self.history = history
        self.projects = projects
        self.home = home
        self.jobs: dict[str, BackfillJob] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start(self, project_id: str) -> dict[str, Any]:
        project = self.projects.projects.get(project_id)
        if not project:
            raise KeyError(project_id)
        for job in self.jobs.values():
            if job.project_id == project_id and job.status in {"queued", "running"}:
                return job.payload()
        job = BackfillJob(str(uuid.uuid4()), project.id, project.name)
        self.jobs[job.id] = job
        self._tasks[job.id] = asyncio.create_task(self._run(job), name=f"history-backfill:{job.id}")
        self._trim()
        return job.payload()

    def get(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        return job.payload()

    def list(self, project_id: str | None = None) -> list[dict[str, Any]]:
        jobs = list(self.jobs.values())
        if project_id:
            jobs = [job for job in jobs if job.project_id == project_id]
        return [
            job.payload()
            for job in sorted(jobs, key=lambda item: item.created_at, reverse=True)
        ]

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        if job.status in {"queued", "running"}:
            job.cancel_requested = True
            job.phase = "cancelling"
        return job.payload()

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _trim(self) -> None:
        finished = sorted(
            (job for job in self.jobs.values() if job.status not in {"queued", "running"}),
            key=lambda job: job.completed_at or job.created_at,
        )
        for job in finished[:-20]:
            self.jobs.pop(job.id, None)
            self._tasks.pop(job.id, None)

    def _owner_for_cwd(self, cwd: str) -> tuple[str | None, bool]:
        candidates = [
            project
            for project in self.projects.projects.values()
            if _contains(project.root, cwd)
        ]
        if not candidates:
            return None, False
        candidates.sort(key=lambda project: len(_normalized_path(project.root)), reverse=True)
        if len(candidates) > 1 and len(_normalized_path(candidates[0].root)) == len(
            _normalized_path(candidates[1].root)
        ):
            return None, True
        return candidates[0].id, False

    async def _run(self, job: BackfillJob) -> None:
        try:
            job.status = "running"
            job.phase = "scanning"
            transcripts = await asyncio.to_thread(
                scan_external_transcripts, self.home, limit=None
            )
            job.scanned = len(transcripts)
            candidates = []
            for item in transcripts:
                owner, ambiguous = self._owner_for_cwd(item.cwd)
                if ambiguous:
                    job.ambiguous += 1
                elif owner == job.project_id:
                    candidates.append(item)
            job.total = len(candidates)
            job.phase = "indexing"
            project = self.projects.projects[job.project_id]
            history_ids = await self.history.native_history_ids()
            for item in candidates:
                if job.cancel_requested:
                    job.status = "cancelled"
                    job.phase = "cancelled"
                    return
                existed = (item.backend, item.native_id) in history_ids
                try:
                    scope = await resolve_project(item.cwd)
                    summary = await asyncio.to_thread(
                        summarize_transcript, item.path, item.backend
                    )
                    await self.history.register_project_scope(scope)
                    await self.history.upsert_external(
                        row_id=item.row_id,
                        native_id=item.native_id,
                        backend=item.backend,
                        name=Path(item.cwd).name or item.backend,
                        cwd=item.cwd,
                        project_id=project.id,
                        spawned_at=item.created_at,
                        transcript_path=str(item.path),
                        repository_id=scope.id,
                        project_label=project.name,
                        project_root=project.root,
                        project_scope_id=scope.id,
                        repo_group_id=scope.repo_group_id,
                        mtime_ns=item.mtime_ns,
                        size=item.size,
                        **summary,
                    )
                    history_id = await self.history.assign_native_project(
                        item.backend,
                        item.native_id,
                        project_id=project.id,
                        project_label=project.name,
                        project_root=project.root,
                    )
                    if not history_id:
                        raise RuntimeError("native transcript did not produce a history row")
                    state, message_count = await self.history.index_transcript(
                        history_id, item.path, item.backend
                    )
                    history_ids[(item.backend, item.native_id)] = history_id
                    if not existed:
                        job.discovered += 1
                    if state == "indexed":
                        job.indexed += 1
                        job.indexed_messages += message_count
                    else:
                        job.unchanged += 1
                except (OSError, ValueError, RuntimeError):
                    job.unreadable += 1
                finally:
                    job.processed += 1
                    await asyncio.sleep(0)
            job.status = "completed"
            job.phase = "completed"
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.phase = "cancelled"
            raise
        except Exception as exc:  # surfaced through the job endpoint
            job.status = "failed"
            job.phase = "failed"
            job.error = str(exc)
        finally:
            job.completed_at = time.time()
