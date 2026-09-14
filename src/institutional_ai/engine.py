"""Explicit institutional lifecycle and controlled organisational mutations."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import fcntl
import time

from .execution import CURRENT_RUN, Cancelled, RunControl
from .live_providers import ProviderDispatcher

from .governance import GovernanceError, GovernancePolicy, default_constitution
from .models import (
    ArtifactStatus,
    Budget,
    Confidence,
    DecisionPayload,
    DirectorReport,
    FindingPayload,
    MemoryRecord,
    MemoryScope,
    Message,
    ObjectionPayload,
    OrganisationEdge,
    ProjectState,
    ProjectStatus,
    QuestionPayload,
    RelationKind,
    ReportBarrier,
    ReportStatus,
    RequestForReviewPayload,
    Review,
    ReviewVerdict,
    RoleRequestPayload,
    RoleRequestRecord,
    RoleRequestStatus,
    RoleTemplate,
    SpecialistReport,
    Task,
    TaskStatus,
    Worker,
    WorkerStatus,
    WorkerModel,
    new_id,
    utc_now,
)
from .providers import (
    Provider,
    ProviderResult,
    WorkAction,
    WorkRequest,
    validate_result,
)
from .store import ProjectStore, canonical_json, sha256_text


class InstitutionalError(RuntimeError):
    pass


class InvalidTransition(InstitutionalError):
    pass


class IndependenceBarrierError(InstitutionalError):
    pass


class BudgetExceeded(InstitutionalError):
    pass


_TRANSITIONS: dict[ProjectStatus, set[ProjectStatus]] = {
    ProjectStatus.CREATED: {ProjectStatus.TEAM_FORMING},
    ProjectStatus.TEAM_FORMING: {ProjectStatus.INDEPENDENT_WORK},
    ProjectStatus.INDEPENDENT_WORK: {ProjectStatus.REPORT_COMMIT},
    ProjectStatus.REPORT_COMMIT: {ProjectStatus.PEER_REVIEW},
    ProjectStatus.PEER_REVIEW: {ProjectStatus.REVISION},
    ProjectStatus.REVISION: {ProjectStatus.DIRECTOR_SYNTHESIS},
    ProjectStatus.DIRECTOR_SYNTHESIS: {ProjectStatus.COMPLETE},
    ProjectStatus.BLOCKED: set(),
    ProjectStatus.FAILED: set(),
    ProjectStatus.COMPLETE: set(),
}


_INITIAL_TASKS: dict[str, tuple[str, str, str]] = {
    "Physicist": (
        "Physical feasibility model",
        "Define a dimensionally consistent vehicle-energy and reserve model for the toy fleet.",
        "physical_model",
    ),
    "Mathematician": (
        "Optimisation formulation",
        "Define the feasible set and objective without assuming empirical weights are valid.",
        "optimization_model",
    ),
    "DataScientist": (
        "Evidence and evaluation",
        "Define what demand data can support and a leakage-safe evaluation strategy.",
        "statistical_claim",
    ),
    "SoftwareEngineer": (
        "Implementation strategy",
        "Propose the smallest reliable implementation and observable acceptance checks.",
        "implementation_feasibility",
    ),
    "RedTeam": (
        "Independent adversarial assessment",
        "Identify failure modes and claims the available evidence cannot support.",
        "adversarial_analysis",
    ),
}

_ROLE_NAMES = {
    "ResearchDirector": "Research Director",
    "DataScientist": "Data Scientist",
    "SoftwareEngineer": "Software Engineer",
    "RedTeam": "Red Team",
    "OperationsResearcher": "Operations Researcher",
}


class InstitutionalEngine:
    def __init__(self, store: ProjectStore, provider: Provider | None = None):
        self.store = store
        self.provider = provider or ProviderDispatcher()
        self.provider.preflight()

    @classmethod
    def at(cls, data_dir: str | Path) -> InstitutionalEngine:
        return cls(ProjectStore(data_dir))

    def create_project(self, mission: str, worker_models: dict[str, WorkerModel] | None = None) -> ProjectState:
        project = ProjectState(mission=mission, constitution=default_constitution(), worker_models=worker_models or {})
        if set(project.worker_models) - set(project.constitution.allowed_role_templates):
            raise InstitutionalError("worker model settings contain an unknown role")
        for memory in self.store.load_organisation_memory()[-12:]:
            project.memories[memory.id] = memory
        self.store.create(project)
        self._event(project, "project.created", "system", project.id, {"mission": mission})
        self._add_memory(
            project,
            MemoryScope.PROJECT,
            project.id,
            "Treat the demonstration as synthetic evidence, not a deployment claim.",
            ["evidence", "integrity", "fleet"],
            [],
        )
        self.store.save(project)
        return project

    def run(self, project_id: str, control: RunControl | None = None, *, recover: bool = False) -> ProjectState:
        # ponytail: one advisory lock per data directory; use a durable scheduler for multiple hosts.
        with (self.store.root / ".run.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise InstitutionalError("another project is already running") from exc
            try:
                if recover:
                    self._prepare_recovery(project_id)
                return self._run(project_id, control)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _run(self, project_id: str, control: RunControl | None = None) -> ProjectState:
        project = self.store.load(project_id)
        if project.status == ProjectStatus.COMPLETE:
            return project
        if project.status in {ProjectStatus.FAILED, ProjectStatus.BLOCKED}:
            raise InstitutionalError(
                f"project is {project.status}; use recover after fixing the cause"
            )
        control = control or RunControl()
        control.started = time.monotonic()
        control.seconds = min(control.seconds, max(0, 900 - project.execution_seconds))
        token = CURRENT_RUN.set(control)
        try:
            control.check()
            if project.status in {ProjectStatus.CREATED, ProjectStatus.TEAM_FORMING}:
                self._form_team(project)
            if project.status == ProjectStatus.INDEPENDENT_WORK:
                self._independent_work(project)
            if project.status == ProjectStatus.REPORT_COMMIT:
                if not self._require_barrier(project).unlocked:
                    raise IndependenceBarrierError("report commits are incomplete")
                self.transition(project, ProjectStatus.PEER_REVIEW, project.director_worker_id or "system")
            if project.status == ProjectStatus.PEER_REVIEW:
                self._peer_review(project)
            if project.status == ProjectStatus.REVISION:
                self._revision(project)
            if project.status == ProjectStatus.DIRECTOR_SYNTHESIS:
                self._director_synthesis(project)
        except Exception as exc:
            self._fail(project, exc)
            raise
        finally:
            project.execution_seconds += time.monotonic() - control.started
            CURRENT_RUN.reset(token)
            self.store.save(project)
            if project.status == ProjectStatus.COMPLETE:
                self.store.write_manifest(project)
        return project

    def recover(self, project_id: str) -> ProjectState:
        return self.run(project_id, recover=True)

    def _prepare_recovery(self, project_id: str) -> None:
        project = self.store.load(project_id)
        if (
            project.status not in {ProjectStatus.FAILED, ProjectStatus.BLOCKED}
            or project.resume_from is None
        ):
            raise InstitutionalError(
                "only a failed or blocked project with a checkpoint can recover"
            )
        resume = project.resume_from
        project.status = resume
        project.failure = None
        project.resume_from = None
        self._event(
            project,
            "project.recovered",
            project.director_worker_id or "system",
            project.id,
            {"resumed_at": resume.value},
        )
        self.store.save(project)

    def block(self, project_id: str, reason: str, actor_id: str) -> ProjectState:
        project = self.store.load(project_id)
        if project.status in {ProjectStatus.COMPLETE, ProjectStatus.FAILED, ProjectStatus.BLOCKED}:
            raise InstitutionalError(f"cannot block a project in state {project.status}")
        checkpoint = project.status
        project.status = ProjectStatus.BLOCKED
        project.resume_from = checkpoint
        project.failure = reason
        self._event(
            project,
            "project.blocked",
            actor_id,
            project.id,
            {"checkpoint": checkpoint.value, "reason": reason},
        )
        self.store.save(project)
        return project

    def transition(self, project: ProjectState, target: ProjectStatus, actor_id: str) -> None:
        if target not in _TRANSITIONS[project.status]:
            raise InvalidTransition(f"cannot move from {project.status} to {target}")
        previous = project.status
        project.status = target
        self._event(
            project,
            "project.transitioned",
            actor_id,
            project.id,
            {"from": previous.value, "to": target.value},
        )
        self.store.save(project)

    def add_worker(
        self,
        project: ProjectState,
        template: RoleTemplate,
        created_by: str,
        *,
        worker_cls: type[Worker] = Worker,
    ) -> Worker:
        if project.status != ProjectStatus.TEAM_FORMING:
            raise GovernanceError("workers may be added only during governed team formation")
        allowed_template = project.constitution.allowed_role_templates.get(template.role)
        if allowed_template != template:
            raise GovernanceError("worker role must use an allowlisted template unchanged")
        if created_by != "system":
            actor = project.workers.get(created_by)
            if actor is None or actor.role not in project.constitution.role_creators:
                raise GovernanceError("worker creator lacks organisational authority")
        worker_id = new_id("worker")
        worker = worker_cls(
            id=worker_id,
            role=template.role,
            name=_ROLE_NAMES.get(template.role, template.role),
            mission=project.mission,
            expertise=template.expertise,
            jurisdiction=template.jurisdiction,
            owns=template.owns,
            must_review=template.must_review,
            cannot_approve=template.cannot_approve,
            allowed_tools=template.allowed_tools,
            workspace=f"workspaces/{worker_id}",
            current_projects=[project.id],
            confidence=Confidence(score=0.5, rationale="No project work completed yet."),
            created_by=created_by,
        )
        if binding := project.worker_models.get(worker.role):
            worker.provider = binding.provider
            worker.model = binding.model
            worker.allowed_tools = []
        project.workers[worker.id] = worker
        project.worker_budgets[worker.id] = Budget(max_calls=8, max_tokens=100_000)
        self.store.ensure_workspace(project, worker.id)
        self._event(
            project,
            "worker.created",
            created_by,
            worker.id,
            {"role": worker.role, "jurisdiction": worker.jurisdiction},
        )
        return worker

    def send_message(self, project: ProjectState, message: Message) -> Message:
        if message.project_id != project.id:
            raise InstitutionalError("message belongs to another project")
        if message.from_worker_id not in project.workers:
            raise InstitutionalError("message sender is not a project worker")
        if unknown := set(message.to_worker_ids) - set(project.workers):
            raise InstitutionalError(f"unknown message recipients: {sorted(unknown)}")
        project.messages[message.id] = message
        project.workers[message.from_worker_id].outbox.append(message.id)
        for recipient in message.to_worker_ids:
            project.workers[recipient].inbox.append(message.id)
        self._event(
            project,
            "message.sent",
            message.from_worker_id,
            message.id,
            {"kind": message.kind, "recipients": message.to_worker_ids},
        )
        return message

    def visible_peer_reports(self, project: ProjectState, worker_id: str) -> list[SpecialistReport]:
        if worker_id not in project.workers:
            raise InstitutionalError(f"unknown worker {worker_id}")
        if project.barrier is None or not project.barrier.unlocked:
            raise IndependenceBarrierError("peer reports remain sealed until every commit arrives")
        return [report for report in project.reports.values() if report.worker_id != worker_id]

    def organisation_at(
        self, project: ProjectState, event_sequence: int | None = None
    ) -> list[OrganisationEdge]:
        sequence = event_sequence or len(project.audit_events)
        return [
            edge
            for edge in project.graph_edges
            if edge.valid_from_event <= sequence
            and (edge.valid_to_event is None or edge.valid_to_event > sequence)
        ]

    def _form_team(self, project: ProjectState) -> None:
        if project.status == ProjectStatus.CREATED:
            self.transition(project, ProjectStatus.TEAM_FORMING, "system")
        templates = project.constitution.allowed_role_templates
        if project.director_worker_id is not None:
            self._plan_team(project)
            return
        director = self.add_worker(project, templates["ResearchDirector"], "system")
        project.director_worker_id = director.id
        director_task = self._add_task(
            project,
            director,
            "Govern mission and synthesize evidence",
            "Form the team, protect independence, resolve process blockers, and issue judgement.",
            "director_judgement",
        )
        director_task.status = TaskStatus.ACTIVE
        for role, (title, description, subject) in _INITIAL_TASKS.items():
            worker = self.add_worker(project, templates[role], director.id)
            self._add_task(project, worker, title, description, subject)
            self._add_edge(
                project,
                worker.id,
                director.id,
                RelationKind.REPORTS_TO,
                "Mission accountability",
                director.id,
            )
        self.store.save(project)
        self._plan_team(project)

    def _plan_team(self, project: ProjectState) -> None:
        director = self._worker_by_role(project, "ResearchDirector")
        mathematician = self._worker_by_role(project, "Mathematician")
        planning = self._call_provider(
            project,
            WorkRequest(
                action=WorkAction.PLAN,
                project_id=project.id,
                mission=project.mission,
                worker=mathematician,
                task=self._task_for(project, mathematician.id),
                memories=self.store.retrieve_memory(project, mathematician.id, {"fleet"}),
            ),
        )
        for requested in planning.role_requests:
            if any(worker.role == requested.requested_role for worker in project.workers.values()):
                continue
            role_request = self.submit_role_request(project, mathematician, director, requested)
            self.decide_role_request(project, role_request, director, RoleRequestStatus.APPROVED)
        # Review obligations are policy, not optional model suggestions. Staff any missing reviewer.
        staffed = {worker.role for worker in project.workers.values()}
        required_roles = {role for roles in project.constitution.mandatory_reviewers.values() for role in roles}
        for role in sorted(required_roles - staffed):
            worker = self.add_worker(project, project.constitution.allowed_role_templates[role], director.id)
            self._add_task(project, worker, "Mandatory review expertise", "Assess fleet solver feasibility and bounded fallback.", "operations_research")
            self._add_edge(project, worker.id, director.id, RelationKind.REPORTS_TO, "Mandatory reviewer required by constitution", director.id)
        required = [
            worker.id for worker in project.workers.values() if worker.role != "ResearchDirector"
        ]
        project.barrier = ReportBarrier(required_worker_ids=required)
        self._event(
            project,
            "barrier.created",
            director.id,
            project.id,
            {"round": 1, "required_worker_ids": required},
        )
        self.transition(project, ProjectStatus.INDEPENDENT_WORK, director.id)

    def _independent_work(self, project: ProjectState) -> None:
        barrier = self._require_barrier(project)
        for worker_id in barrier.required_worker_ids:
            if worker_id in barrier.committed_report_ids:
                continue
            worker = project.workers[worker_id]
            worker.status = WorkerStatus.WORKING
            task = self._task_for(project, worker_id)
            task.status = TaskStatus.ACTIVE
            result = self._call_provider(
                project,
                WorkRequest(
                    action=WorkAction.ANALYSE,
                    project_id=project.id,
                    mission=project.mission,
                    worker=worker,
                    task=task,
                    visible_reports=[],
                    assigned_messages=self._inbox(project, worker_id),
                    memories=self.store.retrieve_memory(
                        project, worker_id, {"fleet", "evidence", task.subject_type}
                    ),
                ),
            )
            if result.report is None or result.artifact_markdown is None:
                raise InstitutionalError(f"provider returned no report for {worker.role}")
            self._commit_initial_report(project, result.report, result.artifact_markdown)
            self.store.save(project)
        self.transition(
            project,
            ProjectStatus.REPORT_COMMIT,
            project.director_worker_id or "system",
        )
        if not barrier.unlocked:
            raise IndependenceBarrierError("report barrier did not unlock")
        self.transition(
            project,
            ProjectStatus.PEER_REVIEW,
            project.director_worker_id or "system",
        )

    def _peer_review(self, project: ProjectState) -> None:
        barrier = self._require_barrier(project)
        if not barrier.unlocked:
            raise IndependenceBarrierError("peer review cannot begin before barrier unlock")
        physicist = self._worker_by_role(project, "Physicist")
        mathematician = self._worker_by_role(project, "Mathematician")
        physical_report = self._latest_report_for(project, physicist.id)
        question = self.send_message(
            project,
            Message(
                project_id=project.id,
                from_worker_id=physicist.id,
                to_worker_ids=[mathematician.id],
                subject="Validity regime for the reserve approximation",
                context_artifact_ids=physical_report.artifact_refs,
                payload=QuestionPayload(
                    question=(
                        "Which parameter bounds keep the reserve constraint conservative rather "
                        "than merely dimensionally consistent?"
                    ),
                    blocking=True,
                ),
            ),
        )
        self.send_message(
            project,
            Message(
                project_id=project.id,
                from_worker_id=mathematician.id,
                to_worker_ids=[physicist.id],
                subject="Bound required before calibration",
                context_artifact_ids=physical_report.artifact_refs,
                in_reply_to=question.id,
                payload=FindingPayload(
                    finding=(
                        "Dimension checking cannot establish conservatism; impose an explicit "
                        "upper confidence bound on energy-per-distance and test its coverage."
                    ),
                    evidence=[physical_report.id],
                ),
            ),
        )
        initial_reports = [
            project.reports[report_id] for report_id in barrier.committed_report_ids.values()
        ]
        for report in initial_reports:
            was_disputed = report.status == ReportStatus.DISPUTED
            if not was_disputed:
                report.status = ReportStatus.UNDER_REVIEW
            owner = project.workers[report.worker_id]
            reviewer_roles = GovernancePolicy(project.constitution).required_reviewer_roles(
                report.subject_type
            )
            for role in reviewer_roles:
                reviewer = self._worker_by_role(project, role)
                if reviewer.id == owner.id:
                    continue
                if any(review.report_id == report.id and review.reviewer_worker_id == reviewer.id
                       for review in project.reviews.values()):
                    continue
                review_request = self.send_message(
                    project,
                    Message(
                        project_id=project.id,
                        from_worker_id=project.director_worker_id or "system",
                        to_worker_ids=[reviewer.id],
                        subject=f"Review {owner.role}",
                        context_artifact_ids=report.artifact_refs,
                        payload=RequestForReviewPayload(
                            report_id=report.id,
                            required_focus=[report.subject_type],
                        ),
                    ),
                )
                result = self._call_provider(
                    project,
                    WorkRequest(
                        action=WorkAction.REVIEW,
                        project_id=project.id,
                        mission=project.mission,
                        worker=reviewer,
                        task=self._task_for(project, reviewer.id),
                        visible_reports=[report],
                        assigned_messages=[review_request],
                        memories=self.store.retrieve_memory(
                            project, reviewer.id, {report.subject_type, "evidence"}
                        ),
                        report_to_review=report,
                    ),
                )
                if result.review is None:
                    raise InstitutionalError(f"provider returned no review from {role}")
                self._record_review(project, result.review)
                self.store.save(project)
        for report in initial_reports:
            reviews = self._reviews_for(project, report.id)
            if report.status == ReportStatus.DISPUTED or any(review.verdict == ReviewVerdict.DISPUTE for review in reviews):
                report.status = ReportStatus.DISPUTED
            elif any(review.verdict == ReviewVerdict.CHANGES_REQUESTED for review in reviews):
                report.status = ReportStatus.CHANGES_REQUESTED
            else:
                report.status = ReportStatus.ACCEPTED
            self._event(
                project,
                "report.review_status_changed",
                project.director_worker_id or "system",
                report.id,
                {"status": report.status.value},
            )
        GovernancePolicy(project.constitution).validate_review_coverage(project)
        self.transition(
            project,
            ProjectStatus.REVISION,
            project.director_worker_id or "system",
        )

    def _revision(self, project: ProjectState) -> None:
        change_requests = [
            report
            for report in project.reports.values()
            if report.status == ReportStatus.CHANGES_REQUESTED
        ]
        for prior in change_requests:
            if any(report.supersedes_report_id == prior.id for report in project.reports.values()):
                continue
            worker = project.workers[prior.worker_id]
            reviews = self._reviews_for(project, prior.id)
            result = self._call_provider(
                project,
                WorkRequest(
                    action=WorkAction.REVISE,
                    project_id=project.id,
                    mission=project.mission,
                    worker=worker,
                    task=project.tasks[prior.task_id],
                    visible_reports=self.visible_peer_reports(project, worker.id),
                    assigned_messages=self._inbox(project, worker.id),
                    memories=self.store.retrieve_memory(
                        project, worker.id, {prior.subject_type, "evidence"}
                    ),
                    prior_report=prior,
                    reviews=reviews,
                ),
            )
            if result.report is None or result.artifact_markdown is None:
                raise InstitutionalError(f"provider returned no revision for {worker.role}")
            revised = result.report
            if revised.id in project.reports:
                raise InstitutionalError("revision id already exists")
            GovernancePolicy(project.constitution).validate_report_evidence(revised.evidence)
            artifact = self.store.write_artifact(
                project,
                worker.id,
                revised.task_id,
                f"reports/revision-v{revised.version}.md",
                result.artifact_markdown,
                dependencies=prior.artifact_refs,
                reviewers=[review.reviewer_worker_id for review in reviews],
                status=ArtifactStatus.REVIEWED,
            )
            revised.artifact_refs = [*prior.artifact_refs, artifact.id]
            revised.content_sha256 = self._report_hash(revised)
            project.reports[revised.id] = revised
            self._event(
                project,
                "report.revised",
                worker.id,
                revised.id,
                {"version": revised.version, "supersedes": prior.id},
            )
            self.store.save(project)
        self.transition(
            project,
            ProjectStatus.DIRECTOR_SYNTHESIS,
            project.director_worker_id or "system",
        )

    def _director_synthesis(self, project: ProjectState) -> None:
        if project.director_worker_id is None:
            raise InstitutionalError("project has no director")
        director = project.workers[project.director_worker_id]
        current_reports = self._current_reports(project)
        result = self._call_provider(
            project,
            WorkRequest(
                action=WorkAction.SYNTHESIZE,
                project_id=project.id,
                mission=project.mission,
                worker=director,
                task=self._task_for(project, director.id),
                visible_reports=current_reports,
                assigned_messages=self._inbox(project, director.id),
                memories=self.store.retrieve_memory(project, director.id, {"evidence", "fleet"}),
                reviews=list(project.reviews.values()),
            ),
        )
        if result.director_report is None:
            raise InstitutionalError("provider returned no Director report")
        report = result.director_report
        GovernancePolicy(project.constitution).validate_director_report(project, report)
        markdown = self._render_director_report(report)
        task = self._task_for(project, director.id)
        artifact = self.store.write_artifact(
            project,
            director.id,
            task.id,
            "reports/director-final.md",
            markdown,
            dependencies=report.artifact_refs,
            reviewers=[review.reviewer_worker_id for review in project.reviews.values()],
            status=ArtifactStatus.REVIEWED,
        )
        report.artifact_refs.append(artifact.id)
        project.director_report = report
        task.status = TaskStatus.COMPLETE
        for worker in project.workers.values():
            worker.status = WorkerStatus.COMPLETE
        self._event(
            project,
            "director_report.committed",
            director.id,
            report.id,
            {
                "source_report_ids": report.source_report_ids,
                "source_review_ids": report.source_review_ids,
                "disagreement_count": len(report.disagreements),
            },
        )
        lesson = MemoryRecord(
            scope=MemoryScope.ORGANISATION,
            owner_id="institutional-ai",
            content=(
                "Fleet studies must preserve the gap between toy feasibility and calibrated "
                "operational evidence; shifted-demand and reserve stress tests are high-value."
            ),
            tags=["fleet", "evidence", "review_outcome"],
            source_ids=[report.id],
        )
        project.memories[lesson.id] = lesson
        self.store.append_organisation_memory(lesson)
        self.transition(project, ProjectStatus.COMPLETE, director.id)
        self.store.write_manifest(project)

    def _commit_initial_report(
        self, project: ProjectState, report: SpecialistReport, markdown: str
    ) -> None:
        barrier = self._require_barrier(project)
        if report.worker_id not in barrier.required_worker_ids:
            raise IndependenceBarrierError("worker is not part of this report round")
        if report.worker_id in barrier.committed_report_ids:
            raise IndependenceBarrierError("worker already committed an initial report")
        GovernancePolicy(project.constitution).validate_report_evidence(report.evidence)
        artifact = self.store.write_artifact(
            project,
            report.worker_id,
            report.task_id,
            "reports/independent-v1.md",
            markdown,
        )
        report.artifact_refs.append(artifact.id)
        if report.id in project.reports:
            raise InstitutionalError("report id already exists")
        if report.status != ReportStatus.DISPUTED:
            report.status = ReportStatus.COMMITTED
        report.content_sha256 = self._report_hash(report)
        project.reports[report.id] = report
        barrier.committed_report_ids[report.worker_id] = report.id
        project.tasks[report.task_id].status = TaskStatus.COMPLETE
        worker = project.workers[report.worker_id]
        worker.confidence = report.confidence
        worker.assumptions = report.assumptions
        worker.unresolved_questions = report.unresolved_questions
        worker.status = WorkerStatus.WAITING
        self._event(
            project,
            "report.committed",
            report.worker_id,
            report.id,
            {"version": report.version, "content_sha256": report.content_sha256},
        )
        if set(barrier.committed_report_ids) == set(barrier.required_worker_ids):
            barrier.unlocked = True
            barrier.unlocked_at = utc_now()
            self._event(
                project,
                "barrier.unlocked",
                project.director_worker_id or "system",
                project.id,
                {"round": barrier.round},
            )

    def _record_review(self, project: ProjectState, review: Review) -> None:
        target = project.reports.get(review.report_id)
        if target is None or target.version != review.report_version:
            raise InstitutionalError("review does not target an existing immutable report version")
        reviewer = project.workers[review.reviewer_worker_id]
        required_roles = GovernancePolicy(project.constitution).required_reviewer_roles(
            target.subject_type
        )
        if reviewer.role not in required_roles:
            raise GovernanceError(
                f"{reviewer.role} was not assigned to review {target.subject_type}"
            )
        project.reviews[review.id] = review
        for artifact_id in target.artifact_refs:
            artifact = project.artifacts[artifact_id]
            if reviewer.id not in artifact.reviewers:
                artifact.reviewers.append(reviewer.id)
            artifact.status = ArtifactStatus.REVIEWED
        self._event(
            project,
            "review.committed",
            reviewer.id,
            review.id,
            {"report_id": target.id, "verdict": review.verdict.value},
        )
        if review.objections:
            self.send_message(
                project,
                Message(
                    project_id=project.id,
                    from_worker_id=reviewer.id,
                    to_worker_ids=[
                        target.worker_id,
                        project.director_worker_id or target.worker_id,
                    ],
                    subject=f"Objection to report {target.id}",
                    context_artifact_ids=target.artifact_refs,
                    payload=ObjectionPayload(
                        objection=" ".join(review.objections),
                        evidence=review.evidence,
                        safety_related=review.safety_objection,
                    ),
                ),
            )

    def submit_role_request(
        self,
        project: ProjectState,
        requester: Worker,
        director: Worker,
        payload: RoleRequestPayload,
    ) -> RoleRequestRecord:
        message = self.send_message(
            project,
            Message(
                project_id=project.id,
                from_worker_id=requester.id,
                to_worker_ids=[director.id],
                subject=f"Request specialist: {payload.requested_role}",
                payload=payload,
            ),
        )
        record = RoleRequestRecord(
            project_id=project.id,
            message_id=message.id,
            requested_by_worker_id=requester.id,
            request=payload,
        )
        project.role_requests[record.id] = record
        self._event(
            project,
            "role_request.submitted",
            requester.id,
            record.id,
            {"requested_role": payload.requested_role},
        )
        return record

    def decide_role_request(
        self,
        project: ProjectState,
        request: RoleRequestRecord,
        director: Worker,
        decision: RoleRequestStatus,
    ) -> None:
        if decision in {RoleRequestStatus.REJECTED, RoleRequestStatus.DEFERRED}:
            if request.status != RoleRequestStatus.PENDING:
                raise GovernanceError("role request has already been decided")
            if director.role not in project.constitution.role_creators:
                raise GovernanceError(f"{director.role} cannot decide role requests")
            request.status = decision
            request.decided_by_worker_id = director.id
            request.decision_reason = (
                "Director found no justified gap."
                if decision == RoleRequestStatus.REJECTED
                else "Director deferred the role pending further evidence."
            )
            request.decided_at = utc_now()
            self.send_message(
                project,
                Message(
                    project_id=project.id,
                    from_worker_id=director.id,
                    to_worker_ids=[request.requested_by_worker_id],
                    subject=f"Role request {decision.value.lower()}",
                    payload=DecisionPayload(
                        subject=request.id,
                        outcome=decision.value,
                        rationale=request.decision_reason,
                    ),
                ),
            )
            self._event(
                project,
                f"role_request.{decision.value.lower()}",
                director.id,
                request.id,
            )
            return
        if decision != RoleRequestStatus.APPROVED:
            raise GovernanceError("role request decision must approve, reject, or defer")
        template = GovernancePolicy(project.constitution).require_role_creation(
            director, request, project
        )
        worker = self.add_worker(project, template, director.id)
        task = self._add_task(
            project,
            worker,
            "Decomposition and solver strategy",
            "Design a bounded rolling-horizon fleet optimisation strategy and fallback.",
            "operations_research",
            depends_on=request.request.related_tasks,
        )
        request.status = RoleRequestStatus.APPROVED
        request.decided_by_worker_id = director.id
        decision_reason = "Distinct operations-research jurisdiction closes a material gap."
        request.decision_reason = decision_reason
        request.created_worker_id = worker.id
        request.decided_at = utc_now()
        requester = project.workers[request.requested_by_worker_id]
        self._add_edge(
            project,
            worker.id,
            director.id,
            RelationKind.REPORTS_TO,
            "Director-authorised specialist role",
            director.id,
        )
        self._add_edge(
            project,
            worker.id,
            requester.id,
            RelationKind.CONSULTS,
            "Solver strategy consultation",
            director.id,
        )
        self.send_message(
            project,
            Message(
                project_id=project.id,
                from_worker_id=director.id,
                to_worker_ids=[requester.id, worker.id],
                subject=f"Role request approved: {worker.role}",
                payload=DecisionPayload(
                    subject=request.id,
                    outcome="APPROVED",
                    rationale=decision_reason,
                ),
            ),
        )
        self._event(
            project,
            "organisation.changed",
            director.id,
            worker.id,
            {"change": "role_created", "role_request_id": request.id, "task_id": task.id},
        )

    def _add_task(
        self,
        project: ProjectState,
        worker: Worker,
        title: str,
        description: str,
        subject_type: str,
        *,
        depends_on: list[str] | None = None,
    ) -> Task:
        task = Task(
            project_id=project.id,
            owner_worker_id=worker.id,
            title=title,
            description=description,
            subject_type=subject_type,
            depends_on=depends_on or [],
        )
        project.tasks[task.id] = task
        self._event(
            project,
            "task.assigned",
            project.director_worker_id or "system",
            task.id,
            {"owner_worker_id": worker.id, "subject_type": subject_type},
        )
        return task

    def _add_edge(
        self,
        project: ProjectState,
        from_worker_id: str,
        to_worker_id: str,
        relation: RelationKind,
        reason: str,
        actor_id: str,
    ) -> OrganisationEdge:
        if from_worker_id not in project.workers or to_worker_id not in project.workers:
            raise InstitutionalError("organisation edge references an unknown worker")
        edge = OrganisationEdge(
            from_worker_id=from_worker_id,
            to_worker_id=to_worker_id,
            relation=relation,
            reason=reason,
            valid_from_event=len(project.audit_events) + 1,
        )
        project.graph_edges.append(edge)
        self._event(
            project,
            "organisation.edge_added",
            actor_id,
            edge.id,
            {"from": from_worker_id, "to": to_worker_id, "relation": relation.value},
        )
        return edge

    def _call_provider(self, project: ProjectState, request: WorkRequest) -> ProviderResult:
        control = CURRENT_RUN.get()
        if control:
            control.check()
        if request.action == WorkAction.ANALYSE and (request.visible_reports or request.reviews or request.report_to_review or request.prior_report):
            raise IndependenceBarrierError("independent analysis cannot contain peer conclusions")
        if request.action in {WorkAction.REVIEW, WorkAction.REVISE, WorkAction.SYNTHESIZE} and not self._require_barrier(project).unlocked:
            raise IndependenceBarrierError("review and synthesis require all independent commits")
        provider = self.provider.select(request) if isinstance(self.provider, ProviderDispatcher) else self.provider
        worker_budget = project.worker_budgets[request.worker.id]
        self._authorize_budget(project.project_budget)
        self._authorize_budget(worker_budget)
        self._event(
            project,
            "provider.call_started",
            request.worker.id,
            project.id,
            {
                "provider": provider.name,
                "model": request.worker.model,
                "action": request.action.value,
                "visible_report_ids": [report.id for report in request.visible_reports],
                "memory_ids": [memory.id for memory in request.memories],
            },
        )
        # Count attempts, including failures, before invoking anything billable.
        project.project_budget.used_calls += 1
        worker_budget.used_calls += 1
        self.store.save(project)
        started = time.monotonic()
        try:
            result = provider.run(request.model_copy(deep=True))
            validate_result(request, result)
            if control:
                control.check()
        finally:
            elapsed = time.monotonic() - started
            project.project_budget.used_wall_seconds += elapsed
            worker_budget.used_wall_seconds += elapsed
        result.usage.calls = 0  # Already reserved above; never trust model-supplied call accounting.
        result.usage.wall_seconds = 0  # Accounted using the host's monotonic clock.
        self._ensure_usage_fits(project.project_budget, result)
        self._ensure_usage_fits(worker_budget, result)
        self._record_usage(project.project_budget, result)
        self._record_usage(worker_budget, result)
        self._event(
            project,
            "provider.call_completed",
            request.worker.id,
            project.id,
            {
                "provider": provider.name,
                "model": result.model or request.worker.model,
                "model_source": result.model_source or "provider_configuration",
                "action": request.action.value,
                "usage": {**result.usage.model_dump(), "calls": 1, "wall_seconds": elapsed},
            },
        )
        return result

    @staticmethod
    def _authorize_budget(budget: Budget) -> None:
        if budget.used_calls + 1 > budget.max_calls:
            raise BudgetExceeded("model-call budget exhausted")
        if budget.used_tokens >= budget.max_tokens:
            raise BudgetExceeded("token budget exhausted")
        if budget.used_wall_seconds >= budget.max_wall_seconds:
            raise BudgetExceeded("wall-time budget exhausted")

    @staticmethod
    def _ensure_usage_fits(budget: Budget, result: ProviderResult) -> None:
        if (
            budget.used_calls + result.usage.calls > budget.max_calls
            or budget.used_tokens + result.usage.total_tokens > budget.max_tokens
            or budget.used_wall_seconds + result.usage.wall_seconds > budget.max_wall_seconds
        ):
            raise BudgetExceeded("provider result exceeded the remaining budget")

    @staticmethod
    def _record_usage(budget: Budget, result: ProviderResult) -> None:
        budget.used_calls += result.usage.calls
        budget.used_tokens += result.usage.total_tokens
        budget.used_wall_seconds += result.usage.wall_seconds

    def _fail(self, project: ProjectState, error: Exception) -> None:
        checkpoint = project.status
        project.status = ProjectStatus.BLOCKED if isinstance(error, Cancelled) else ProjectStatus.FAILED
        project.resume_from = checkpoint
        project.failure = f"{type(error).__name__}: {error}"
        self._event(
            project,
            "project.blocked" if isinstance(error, Cancelled) else "project.failed",
            project.director_worker_id or "system",
            project.id,
            {"checkpoint": checkpoint.value, "error": project.failure},
        )
        self.store.save(project)

    def _add_memory(
        self,
        project: ProjectState,
        scope: MemoryScope,
        owner_id: str,
        content: str,
        tags: list[str],
        source_ids: list[str],
    ) -> MemoryRecord:
        memory = MemoryRecord(
            scope=scope,
            owner_id=owner_id,
            content=content,
            tags=tags,
            source_ids=source_ids,
        )
        if scope == MemoryScope.WORKER:
            if owner_id not in project.workers:
                raise InstitutionalError(f"unknown worker memory owner {owner_id}")
            project.workers[owner_id].memory.append(memory.id)
        project.memories[memory.id] = memory
        self._event(
            project,
            "memory.recorded",
            owner_id,
            memory.id,
            {"scope": scope.value, "tags": tags},
        )
        return memory

    def _event(
        self,
        project: ProjectState,
        event_type: str,
        actor_id: str,
        entity_id: str,
        details: dict[str, object] | None = None,
    ) -> None:
        self.store.record_event(project, event_type, actor_id, entity_id, details)

    @staticmethod
    def _require_barrier(project: ProjectState) -> ReportBarrier:
        if project.barrier is None:
            raise IndependenceBarrierError("project has no independent report barrier")
        return project.barrier

    @staticmethod
    def _worker_by_role(project: ProjectState, role: str) -> Worker:
        try:
            return next(worker for worker in project.workers.values() if worker.role == role)
        except StopIteration as exc:
            raise InstitutionalError(f"required role {role} is not staffed") from exc

    @staticmethod
    def _task_for(project: ProjectState, worker_id: str) -> Task:
        try:
            return next(
                task for task in project.tasks.values() if task.owner_worker_id == worker_id
            )
        except StopIteration as exc:
            raise InstitutionalError(f"worker {worker_id} has no task") from exc

    @staticmethod
    def _inbox(project: ProjectState, worker_id: str) -> list[Message]:
        message_ids = project.workers[worker_id].inbox[-20:]
        return [project.messages[item] for item in message_ids]

    @staticmethod
    def _reviews_for(project: ProjectState, report_id: str) -> list[Review]:
        return [review for review in project.reviews.values() if review.report_id == report_id]

    @staticmethod
    def _latest_report_for(project: ProjectState, worker_id: str) -> SpecialistReport:
        reports = [report for report in project.reports.values() if report.worker_id == worker_id]
        if not reports:
            raise InstitutionalError(f"worker {worker_id} has no report")
        return max(reports, key=lambda report: report.version)

    def _current_reports(self, project: ProjectState) -> list[SpecialistReport]:
        return [
            self._latest_report_for(project, worker_id)
            for worker_id in self._require_barrier(project).required_worker_ids
        ]

    @staticmethod
    def _report_hash(report: SpecialistReport) -> str:
        content = report.model_dump(mode="json", exclude={"content_sha256", "status"})
        return sha256_text(canonical_json(content))

    @staticmethod
    def _render_director_report(report: DirectorReport) -> str:
        def section(title: str, values: Iterable[str]) -> str:
            return f"## {title}\n\n" + "\n".join(f"- {value}" for value in values) + "\n\n"

        return (
            "# Director report\n\n"
            + section("Consensus", report.consensus)
            + section("Disagreements", report.disagreements)
            + section("Evidence", report.evidence)
            + section("Assumptions", report.assumptions)
            + section("Risks", report.risks)
            + section("Unresolved questions", report.unresolved_questions)
            + f"## Director judgement\n\n{report.director_judgement}\n\n"
            + f"## Recommended action\n\n{report.recommended_action}\n"
        )
