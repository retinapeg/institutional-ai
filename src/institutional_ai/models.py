"""Typed institutional state.

The models are deliberately boring: they are the durable contract shared by the
engine, providers, filesystem store, API, and dashboard.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ProjectStatus(StrEnum):
    CREATED = "CREATED"
    TEAM_FORMING = "TEAM_FORMING"
    INDEPENDENT_WORK = "INDEPENDENT_WORK"
    REPORT_COMMIT = "REPORT_COMMIT"
    PEER_REVIEW = "PEER_REVIEW"
    REVISION = "REVISION"
    DIRECTOR_SYNTHESIS = "DIRECTOR_SYNTHESIS"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class WorkerStatus(StrEnum):
    IDLE = "IDLE"
    WORKING = "WORKING"
    WAITING = "WAITING"
    REVIEWING = "REVIEWING"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"


class TaskStatus(StrEnum):
    OPEN = "OPEN"
    ACTIVE = "ACTIVE"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"


class ArtifactStatus(StrEnum):
    DRAFT = "DRAFT"
    COMMITTED = "COMMITTED"
    REVIEWED = "REVIEWED"


class ReportStatus(StrEnum):
    DRAFT = "DRAFT"
    COMMITTED = "COMMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    REVISED = "REVISED"
    ACCEPTED = "ACCEPTED"
    DISPUTED = "DISPUTED"


class ReviewVerdict(StrEnum):
    ACCEPT = "ACCEPT"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    DISPUTE = "DISPUTE"


class RelationKind(StrEnum):
    REPORTS_TO = "reports_to"
    REVIEWS = "reviews"
    CONSULTS = "consults"
    DEPENDS_ON = "depends_on"
    OWNS_INTERFACE_WITH = "owns_interface_with"
    MAY_ESCALATE_TO = "may_escalate_to"


class RoleRequestStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"


class MemoryScope(StrEnum):
    WORKER = "WORKER"
    PROJECT = "PROJECT"
    ORGANISATION = "ORGANISATION"


class Confidence(StrictModel):
    score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)


class Usage(StrictModel):
    calls: int = Field(default=1, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    wall_seconds: float = Field(default=0.0, ge=0.0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class Budget(StrictModel):
    max_calls: int = Field(default=100, ge=1)
    max_tokens: int = Field(default=100_000, ge=1)
    max_wall_seconds: float = Field(default=60.0, gt=0)
    used_calls: int = Field(default=0, ge=0)
    used_tokens: int = Field(default=0, ge=0)
    used_wall_seconds: float = Field(default=0.0, ge=0)


class Worker(StrictModel):
    """Persistent worker identity; subclass this for custom role behaviour."""

    id: str = Field(default_factory=lambda: new_id("worker"))
    role: str = Field(min_length=1)
    name: str = Field(min_length=1)
    mission: str = Field(min_length=1)
    expertise: list[str] = Field(default_factory=list)
    jurisdiction: list[str] = Field(default_factory=list)
    owns: list[str] = Field(default_factory=list)
    must_review: list[str] = Field(default_factory=list)
    cannot_approve: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    workspace: str
    memory: list[str] = Field(default_factory=list)
    current_projects: list[str] = Field(default_factory=list)
    inbox: list[str] = Field(default_factory=list)
    outbox: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    confidence: Confidence
    status: WorkerStatus = WorkerStatus.IDLE
    provider: str = "deterministic"
    model: str = "fleet-demo-v1"
    created_by: str
    created_at: datetime = Field(default_factory=utc_now)


class Task(StrictModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    project_id: str
    owner_worker_id: str
    title: str
    description: str
    subject_type: str
    depends_on: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.OPEN
    created_at: datetime = Field(default_factory=utc_now)


class Artifact(StrictModel):
    id: str = Field(default_factory=lambda: new_id("artifact"))
    project_id: str
    creator_worker_id: str
    source_task_id: str
    relative_path: str
    media_type: str = "text/markdown"
    version: int = Field(default=1, ge=1)
    dependencies: list[str] = Field(default_factory=list)
    reviewers: list[str] = Field(default_factory=list)
    status: ArtifactStatus = ArtifactStatus.COMMITTED
    sha256: str
    created_at: datetime = Field(default_factory=utc_now)


class TaskPayload(StrictModel):
    kind: Literal["Task"] = "Task"
    task_id: str
    instructions: str


class QuestionPayload(StrictModel):
    kind: Literal["Question"] = "Question"
    question: str
    blocking: bool = False


class RequestForReviewPayload(StrictModel):
    kind: Literal["RequestForReview"] = "RequestForReview"
    report_id: str
    required_focus: list[str] = Field(default_factory=list)


class IssuePayload(StrictModel):
    kind: Literal["Issue"] = "Issue"
    severity: Literal["low", "medium", "high", "critical"]
    description: str


class ProposalPayload(StrictModel):
    kind: Literal["Proposal"] = "Proposal"
    proposal: str
    expected_value: str


class FindingPayload(StrictModel):
    kind: Literal["Finding"] = "Finding"
    finding: str
    evidence: list[str] = Field(default_factory=list)


class ObjectionPayload(StrictModel):
    kind: Literal["Objection"] = "Objection"
    objection: str
    evidence: list[str] = Field(default_factory=list)
    safety_related: bool = False


class ExperimentRequestPayload(StrictModel):
    kind: Literal["ExperimentRequest"] = "ExperimentRequest"
    hypothesis: str
    method: str
    success_measure: str


class ArtifactReferencePayload(StrictModel):
    kind: Literal["ArtifactReference"] = "ArtifactReference"
    artifact_id: str
    note: str


class DecisionPayload(StrictModel):
    kind: Literal["Decision"] = "Decision"
    subject: str
    outcome: str
    rationale: str


class RoleRequestPayload(StrictModel):
    kind: Literal["RoleRequest"] = "RoleRequest"
    requested_role: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    required_capabilities: list[str] = Field(min_length=1)
    related_tasks: list[str] = Field(min_length=1)
    proposed_jurisdiction: list[str] = Field(default_factory=list)
    proposed_tools: list[str] = Field(default_factory=list)


class EscalationPayload(StrictModel):
    kind: Literal["Escalation"] = "Escalation"
    reason: str
    requested_action: str


MessagePayload = Annotated[
    TaskPayload
    | QuestionPayload
    | RequestForReviewPayload
    | IssuePayload
    | ProposalPayload
    | FindingPayload
    | ObjectionPayload
    | ExperimentRequestPayload
    | ArtifactReferencePayload
    | DecisionPayload
    | RoleRequestPayload
    | EscalationPayload,
    Field(discriminator="kind"),
]


class Message(StrictModel):
    id: str = Field(default_factory=lambda: new_id("message"))
    project_id: str
    from_worker_id: str
    to_worker_ids: list[str] = Field(min_length=1)
    subject: str
    context_artifact_ids: list[str] = Field(default_factory=list)
    payload: MessagePayload
    in_reply_to: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def kind(self) -> str:
        return self.payload.kind


class SpecialistReport(StrictModel):
    id: str = Field(default_factory=lambda: new_id("report"))
    worker_id: str
    project_id: str
    task_id: str
    subject_type: str
    question: str
    conclusion: str
    evidence: list[str]
    artifact_refs: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    confidence: Confidence
    objections: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)
    dissent_from_consensus: list[str] = Field(default_factory=list)
    status: ReportStatus = ReportStatus.DRAFT
    version: int = Field(default=1, ge=1)
    supersedes_report_id: str | None = None
    content_sha256: str | None = None
    timestamp: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def revision_has_parent(self) -> SpecialistReport:
        if self.version > 1 and not self.supersedes_report_id:
            raise ValueError("revised reports must identify the report they supersede")
        return self


class Review(StrictModel):
    id: str = Field(default_factory=lambda: new_id("review"))
    project_id: str
    report_id: str
    report_version: int
    reviewer_worker_id: str
    subject_type: str
    verdict: ReviewVerdict
    findings: list[str]
    objections: list[str] = Field(default_factory=list)
    requested_changes: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: Confidence
    safety_objection: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class RoleRequestRecord(StrictModel):
    id: str = Field(default_factory=lambda: new_id("role_request"))
    project_id: str
    message_id: str
    requested_by_worker_id: str
    request: RoleRequestPayload
    status: RoleRequestStatus = RoleRequestStatus.PENDING
    decided_by_worker_id: str | None = None
    decision_reason: str | None = None
    created_worker_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    decided_at: datetime | None = None


class OrganisationEdge(StrictModel):
    id: str = Field(default_factory=lambda: new_id("edge"))
    from_worker_id: str
    to_worker_id: str
    relation: RelationKind
    reason: str
    valid_from_event: int = Field(ge=1)
    valid_to_event: int | None = Field(default=None, ge=1)


class MemoryRecord(StrictModel):
    id: str = Field(default_factory=lambda: new_id("memory"))
    scope: MemoryScope
    owner_id: str
    content: str
    tags: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class ReportBarrier(StrictModel):
    round: int = Field(default=1, ge=1)
    required_worker_ids: list[str]
    committed_report_ids: dict[str, str] = Field(default_factory=dict)
    unlocked: bool = False
    unlocked_at: datetime | None = None


class RoleTemplate(StrictModel):
    role: str
    expertise: list[str]
    jurisdiction: list[str]
    owns: list[str]
    must_review: list[str] = Field(default_factory=list)
    cannot_approve: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)


class Constitution(StrictModel):
    decision_owners: dict[str, list[str]]
    mandatory_reviewers: dict[str, list[str]]
    forbidden_approvers: dict[str, list[str]]
    role_creators: list[str]
    allowed_role_templates: dict[str, RoleTemplate]
    max_workers: int = Field(default=10, ge=2)
    minimum_evidence_items: int = Field(default=1, ge=1)
    maximum_review_rounds: int = Field(default=2, ge=1)


class DirectorReport(StrictModel):
    id: str = Field(default_factory=lambda: new_id("director_report"))
    project_id: str
    director_worker_id: str
    consensus: list[str]
    disagreements: list[str]
    evidence: list[str]
    assumptions: list[str]
    risks: list[str]
    unresolved_questions: list[str]
    director_judgement: str
    recommended_action: str
    artifact_refs: list[str]
    source_report_ids: list[str]
    source_review_ids: list[str]
    created_at: datetime = Field(default_factory=utc_now)


class AuditEvent(StrictModel):
    sequence: int = Field(ge=1)
    event_type: str
    actor_id: str
    entity_id: str
    details: dict[str, object] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)
    previous_hash: str | None = None
    hash: str


class ProjectState(StrictModel):
    id: str = Field(default_factory=lambda: new_id("project"))
    mission: str = Field(min_length=1)
    status: ProjectStatus = ProjectStatus.CREATED
    director_worker_id: str | None = None
    constitution: Constitution
    workers: dict[str, Worker] = Field(default_factory=dict)
    tasks: dict[str, Task] = Field(default_factory=dict)
    artifacts: dict[str, Artifact] = Field(default_factory=dict)
    messages: dict[str, Message] = Field(default_factory=dict)
    reports: dict[str, SpecialistReport] = Field(default_factory=dict)
    reviews: dict[str, Review] = Field(default_factory=dict)
    role_requests: dict[str, RoleRequestRecord] = Field(default_factory=dict)
    graph_edges: list[OrganisationEdge] = Field(default_factory=list)
    memories: dict[str, MemoryRecord] = Field(default_factory=dict)
    barrier: ReportBarrier | None = None
    project_budget: Budget = Field(default_factory=Budget)
    worker_budgets: dict[str, Budget] = Field(default_factory=dict)
    director_report: DirectorReport | None = None
    audit_events: list[AuditEvent] = Field(default_factory=list)
    failure: str | None = None
    resume_from: ProjectStatus | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
