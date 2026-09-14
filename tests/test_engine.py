import json

import pytest
from pydantic import ValidationError

from institutional_ai.engine import (
    BudgetExceeded,
    IndependenceBarrierError,
    InstitutionalEngine,
    InstitutionalError,
    InvalidTransition,
)
from institutional_ai.governance import GovernanceError, GovernancePolicy
from institutional_ai.models import (
    Confidence,
    MemoryScope,
    Message,
    ProjectStatus,
    QuestionPayload,
    RelationKind,
    ReportStatus,
    RoleRequestStatus,
    SpecialistReport,
    Worker,
)
from institutional_ai.providers import FleetDemoProvider, WorkAction, WorkRequest
from institutional_ai.store import ProjectStore, StoreError, canonical_json, sha256_text

MISSION = "Evaluate a deterministic toy fleet without making deployment claims."


class CustomWorker(Worker):
    def identity(self) -> str:
        return f"{self.role}:{self.id}"


class FailOnceProvider:
    name = "fail-once"

    def __init__(self) -> None:
        self.delegate = FleetDemoProvider()
        self.preflight_calls = 0
        self.calls: list[WorkAction] = []
        self.failed = False

    def preflight(self) -> None:
        self.preflight_calls += 1

    def run(self, request: WorkRequest):
        self.calls.append(request.action)
        if request.action == WorkAction.ANALYSE and not self.failed:
            self.failed = True
            raise RuntimeError("one deterministic provider failure")
        return self.delegate.run(request)


def make_engine(tmp_path, provider=None) -> InstitutionalEngine:
    return InstitutionalEngine(ProjectStore(tmp_path), provider)


def form_team(engine: InstitutionalEngine):
    project = engine.create_project(MISSION)
    engine._form_team(project)
    return project


def task_for(project, worker_id):
    return next(task for task in project.tasks.values() if task.owner_worker_id == worker_id)


def report_for(project, worker_id, *, evidence=None) -> SpecialistReport:
    task = task_for(project, worker_id)
    return SpecialistReport(
        worker_id=worker_id,
        project_id=project.id,
        task_id=task.id,
        subject_type=task.subject_type,
        question=task.description,
        conclusion=f"Independent conclusion from {project.workers[worker_id].role}.",
        evidence=["deterministic source"] if evidence is None else evidence,
        confidence=Confidence(score=0.7, rationale="Bounded test evidence."),
    )


def test_worker_creation_supports_a_custom_subclass(tmp_path):
    engine = make_engine(tmp_path)
    project = engine.create_project(MISSION)
    engine.transition(project, ProjectStatus.TEAM_FORMING, "system")

    worker = engine.add_worker(
        project,
        project.constitution.allowed_role_templates["Physicist"],
        "system",
        worker_cls=CustomWorker,
    )

    assert isinstance(worker, CustomWorker)
    assert worker.identity() == f"Physicist:{worker.id}"
    assert worker.id in project.worker_budgets
    assert (engine.store.project_dir(project.id) / worker.workspace).is_dir()
    assert project.audit_events[-1].event_type == "worker.created"


def test_governance_enforces_jurisdiction_and_approval_ownership(tmp_path):
    engine = make_engine(tmp_path)
    project = engine.create_project(MISSION)
    engine.transition(project, ProjectStatus.TEAM_FORMING, "system")
    physicist = engine.add_worker(
        project, project.constitution.allowed_role_templates["Physicist"], "system"
    )
    data_scientist = engine.add_worker(
        project, project.constitution.allowed_role_templates["DataScientist"], "system"
    )
    policy = GovernancePolicy(project.constitution)

    policy.require_jurisdiction(physicist, "physical_model")
    policy.require_approval_authority(physicist, "physical_validity")
    with pytest.raises(GovernanceError, match="no jurisdiction"):
        policy.require_jurisdiction(physicist, "statistical_claim")
    with pytest.raises(GovernanceError, match="cannot approve"):
        policy.require_approval_authority(data_scientist, "physical_validity")
    with pytest.raises(GovernanceError, match="cannot approve"):
        policy.require_approval_authority(physicist, "statistical_claim")


def test_report_validation_rejects_missing_evidence_and_orphan_revisions(tmp_path):
    engine = make_engine(tmp_path)
    project = form_team(engine)
    worker_id = project.barrier.required_worker_ids[0]

    with pytest.raises(GovernanceError, match="minimum evidence"):
        engine._commit_initial_report(project, report_for(project, worker_id, evidence=[]), "x")
    assert not project.reports
    orphan = report_for(project, worker_id).model_dump()
    orphan["version"] = 2
    with pytest.raises(ValidationError, match="supersede"):
        SpecialistReport.model_validate(orphan)


def test_report_barrier_seals_peers_until_every_initial_commit(tmp_path):
    engine = make_engine(tmp_path)
    project = form_team(engine)
    required = project.barrier.required_worker_ids

    with pytest.raises(IndependenceBarrierError, match="sealed"):
        engine.visible_peer_reports(project, required[0])
    for worker_id in required[:-1]:
        engine._commit_initial_report(project, report_for(project, worker_id), "report")
    with pytest.raises(IndependenceBarrierError, match="sealed"):
        engine.visible_peer_reports(project, required[-1])

    engine._commit_initial_report(project, report_for(project, required[-1]), "report")
    visible = engine.visible_peer_reports(project, required[0])

    assert project.barrier.unlocked is True
    assert project.barrier.unlocked_at is not None
    assert {report.worker_id for report in visible} == set(required[1:])


def test_typed_messages_route_to_sender_and_recipient_mailboxes(tmp_path):
    engine = make_engine(tmp_path)
    project = form_team(engine)
    sender, *recipients = project.barrier.required_worker_ids[:3]
    message = Message(
        project_id=project.id,
        from_worker_id=sender,
        to_worker_ids=recipients,
        subject="A bounded question",
        payload=QuestionPayload(
            question="What evidence would change the conclusion?", blocking=True
        ),
    )

    assert engine.send_message(project, message) is message
    assert message.kind == "Question"
    assert message.id in project.workers[sender].outbox
    assert all(message.id in project.workers[worker_id].inbox for worker_id in recipients)
    assert all(message in engine._inbox(project, worker_id) for worker_id in recipients)

    with pytest.raises(InstitutionalError, match="unknown message recipients"):
        engine.send_message(
            project,
            message.model_copy(update={"id": "message_bad", "to_worker_ids": ["unknown"]}),
        )


def test_dynamic_role_approval_mutates_organisation_not_task_dependencies(tmp_path):
    engine = make_engine(tmp_path)
    project = form_team(engine)
    request = next(iter(project.role_requests.values()))

    assert request.status == RoleRequestStatus.APPROVED
    specialist = project.workers[request.created_worker_id]
    assert specialist.role == "OperationsResearcher"
    task = task_for(project, specialist.id)
    assert task.depends_on == request.request.related_tasks

    specialist_edges = [
        edge for edge in project.graph_edges if edge.from_worker_id == specialist.id
    ]
    assert {edge.relation for edge in specialist_edges} == {
        RelationKind.REPORTS_TO,
        RelationKind.CONSULTS,
    }
    assert not any(edge.relation == RelationKind.DEPENDS_ON for edge in project.graph_edges)
    before_creation = min(edge.valid_from_event for edge in specialist_edges) - 1
    assert not any(
        edge.from_worker_id == specialist.id
        for edge in engine.organisation_at(project, before_creation)
    )
    assert all(edge in engine.organisation_at(project) for edge in specialist_edges)


def test_artifacts_preserve_provenance_and_reject_unsafe_ownership_or_paths(tmp_path):
    engine = make_engine(tmp_path)
    project = form_team(engine)
    owner_id, other_id = project.barrier.required_worker_ids[:2]
    task = task_for(project, owner_id)
    source = engine.store.write_artifact(project, owner_id, task.id, "notes/source.md", "source")
    derived = engine.store.write_artifact(
        project,
        owner_id,
        task.id,
        "notes/derived.md",
        "derived",
        dependencies=[source.id],
        reviewers=[other_id],
    )

    assert derived.creator_worker_id == owner_id
    assert derived.source_task_id == task.id
    assert derived.dependencies == [source.id]
    assert derived.reviewers == [other_id]
    assert derived.sha256 == sha256_text("derived")
    artifact_path = engine.store.project_dir(project.id) / derived.relative_path
    assert artifact_path.read_text(encoding="utf-8") == "derived"

    with pytest.raises(StoreError, match="escapes"):
        engine.store.write_artifact(project, owner_id, task.id, "../../escape.md", "no")
    with pytest.raises(StoreError, match="another worker"):
        engine.store.write_artifact(project, other_id, task.id, "wrong-owner.md", "no")
    assert not (engine.store.project_dir(project.id) / "escape.md").exists()


def test_invalid_project_transition_is_rejected_without_mutation(tmp_path):
    engine = make_engine(tmp_path)
    project = engine.create_project(MISSION)
    event_count = len(project.audit_events)

    with pytest.raises(InvalidTransition, match="CREATED.*COMPLETE"):
        engine.transition(project, ProjectStatus.COMPLETE, "system")

    assert project.status == ProjectStatus.CREATED
    assert len(project.audit_events) == event_count
    assert engine.store.load(project.id).status == ProjectStatus.CREATED


def test_blocked_project_records_checkpoint_and_can_resume(tmp_path):
    engine = make_engine(tmp_path)
    project = engine.create_project(MISSION)
    blocked = engine.block(project.id, "Waiting for a bounded input", "system")

    assert blocked.status == ProjectStatus.BLOCKED
    assert blocked.resume_from == ProjectStatus.CREATED
    assert blocked.audit_events[-1].event_type == "project.blocked"
    assert engine.recover(project.id).status == ProjectStatus.COMPLETE


def test_provider_failure_is_persisted_and_one_retry_recovers(tmp_path):
    provider = FailOnceProvider()
    engine = make_engine(tmp_path, provider)
    project = engine.create_project(MISSION)

    with pytest.raises(RuntimeError, match="one deterministic provider failure"):
        engine.run(project.id)
    failed = engine.store.load(project.id)
    assert failed.status == ProjectStatus.FAILED
    assert failed.resume_from == ProjectStatus.INDEPENDENT_WORK
    assert failed.failure == "RuntimeError: one deterministic provider failure"

    completed = engine.recover(project.id)
    event_types = [event.event_type for event in completed.audit_events]
    assert completed.status == ProjectStatus.COMPLETE
    assert provider.preflight_calls == 1
    assert (
        provider.calls.count(WorkAction.ANALYSE) == len(completed.barrier.required_worker_ids) + 1
    )
    assert "project.failed" in event_types
    assert "project.recovered" in event_types


def test_budget_exhaustion_fails_closed_before_another_provider_call(tmp_path):
    engine = make_engine(tmp_path)
    project = form_team(engine)
    project.project_budget.max_calls = project.project_budget.used_calls
    engine.store.save(project)
    started_before = sum(
        event.event_type == "provider.call_started" for event in project.audit_events
    )

    with pytest.raises(BudgetExceeded, match="budget exhausted"):
        engine.run(project.id)

    failed = engine.store.load(project.id)
    assert failed.status == ProjectStatus.FAILED
    assert failed.resume_from == ProjectStatus.INDEPENDENT_WORK
    assert (
        sum(event.event_type == "provider.call_started" for event in failed.audit_events)
        == started_before
    )


def test_memory_visibility_is_scoped_to_worker_project_and_organisation(tmp_path):
    engine = make_engine(tmp_path)
    project = form_team(engine)
    first, second = project.barrier.required_worker_ids[:2]
    first_memory = engine._add_memory(
        project, MemoryScope.WORKER, first, "first only", ["scope-test"], []
    )
    second_memory = engine._add_memory(
        project, MemoryScope.WORKER, second, "second only", ["scope-test"], []
    )
    project_memory = engine._add_memory(
        project, MemoryScope.PROJECT, project.id, "project", ["scope-test"], []
    )
    organisation_memory = engine._add_memory(
        project,
        MemoryScope.ORGANISATION,
        "institutional-ai",
        "organisation",
        ["scope-test"],
        [],
    )
    engine.store.append_organisation_memory(organisation_memory)

    visible = {memory.id for memory in engine.store.retrieve_memory(project, first, {"scope-test"})}
    assert visible == {first_memory.id, project_memory.id, organisation_memory.id}
    assert first_memory.id in project.workers[first].memory
    assert second_memory.id not in visible
    next_project = engine.create_project("A second deterministic mission")
    assert organisation_memory.id in next_project.memories
    assert first_memory.id not in next_project.memories
    assert project_memory.id not in next_project.memories


def test_demo_preserves_revisions_dissent_citations_and_verifiable_audit(tmp_path):
    engine = make_engine(tmp_path)
    project = engine.create_project(MISSION)
    completed = engine.run(project.id)

    assert completed.status == ProjectStatus.COMPLETE
    transitions = [
        event.details["to"]
        for event in completed.audit_events
        if event.event_type == "project.transitioned"
    ]
    assert transitions == [
        "TEAM_FORMING",
        "INDEPENDENT_WORK",
        "REPORT_COMMIT",
        "PEER_REVIEW",
        "REVISION",
        "DIRECTOR_SYNTHESIS",
        "COMPLETE",
    ]

    revisions = [report for report in completed.reports.values() if report.version == 2]
    assert revisions
    assert all(
        report.status == ReportStatus.REVISED
        and report.supersedes_report_id in completed.reports
        and completed.reports[report.supersedes_report_id].status == ReportStatus.CHANGES_REQUESTED
        for report in revisions
    )
    disputed = [
        report for report in completed.reports.values() if report.status == ReportStatus.DISPUTED
    ]
    assert disputed

    director = completed.director_report
    assert director is not None
    assert set(director.source_report_ids) == {
        engine._latest_report_for(completed, worker_id).id
        for worker_id in completed.barrier.required_worker_ids
    }
    assert {report.id for report in disputed} <= set(director.source_report_ids)
    assert set(director.source_review_ids) == set(completed.reviews)
    safety_review_ids = {
        review.id for review in completed.reviews.values() if review.safety_objection
    }
    assert safety_review_ids <= set(director.source_review_ids)
    assert director.disagreements
    assert any("missingness" in disagreement for disagreement in director.disagreements)

    for expected, event in enumerate(completed.audit_events, 1):
        assert event.sequence == expected
        assert event.previous_hash == (
            completed.audit_events[expected - 2].hash if expected > 1 else None
        )
        body = {
            "sequence": event.sequence,
            "event_type": event.event_type,
            "actor_id": event.actor_id,
            "entity_id": event.entity_id,
            "details": event.details,
            "timestamp": event.timestamp,
            "previous_hash": event.previous_hash,
        }
        assert event.hash == sha256_text(canonical_json(body))

    project_dir = engine.store.project_dir(completed.id)
    event_path = project_dir / "events.jsonl"
    persisted_events = [json.loads(line) for line in event_path.read_text().splitlines()]
    manifest = json.loads((project_dir / "manifest.json").read_text())
    assert [event["hash"] for event in persisted_events] == [
        event.hash for event in completed.audit_events
    ]
    assert manifest["status"] == "COMPLETE"
    assert manifest["event_count"] == len(completed.audit_events)
    assert manifest["state_sha256"] == sha256_text(
        (project_dir / "state.json").read_text(encoding="utf-8")
    )
    assert manifest["event_log_sha256"] == sha256_text(event_path.read_text(encoding="utf-8"))
