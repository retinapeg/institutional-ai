"""Provider boundary and the free, deterministic demonstration provider."""

from __future__ import annotations

from enum import StrEnum
from typing import NotRequired, Protocol, TypedDict

from pydantic import Field

from .models import (
    Confidence,
    DirectorReport,
    MemoryRecord,
    Message,
    ReportStatus,
    Review,
    ReviewVerdict,
    RoleRequestPayload,
    SpecialistReport,
    StrictModel,
    Task,
    Usage,
    Worker,
    new_id,
)


class WorkAction(StrEnum):
    PLAN = "PLAN"
    ANALYSE = "ANALYSE"
    REVIEW = "REVIEW"
    REVISE = "REVISE"
    SYNTHESIZE = "SYNTHESIZE"


class WorkRequest(StrictModel):
    action: WorkAction
    project_id: str
    mission: str
    worker: Worker
    task: Task | None = None
    visible_reports: list[SpecialistReport] = Field(default_factory=list)
    assigned_messages: list[Message] = Field(default_factory=list)
    memories: list[MemoryRecord] = Field(default_factory=list)
    report_to_review: SpecialistReport | None = None
    prior_report: SpecialistReport | None = None
    reviews: list[Review] = Field(default_factory=list)


class ProviderResult(StrictModel):
    report: SpecialistReport | None = None
    review: Review | None = None
    director_report: DirectorReport | None = None
    role_requests: list[RoleRequestPayload] = Field(default_factory=list)
    artifact_markdown: str | None = None
    usage: Usage = Field(default_factory=Usage)


class Provider(Protocol):
    name: str

    def preflight(self) -> None: ...

    def run(self, request: WorkRequest) -> ProviderResult: ...


class _Analysis(TypedDict):
    conclusion: str
    evidence: list[str]
    assumptions: list[str]
    uncertainty: list[str]
    confidence: float
    next: list[str]
    dissent: NotRequired[list[str]]


class FleetDemoProvider:
    """Repeatable institutional behaviour; it never calls a model or network."""

    name = "deterministic"

    def preflight(self) -> None:
        return None

    def run(self, request: WorkRequest) -> ProviderResult:
        usage = Usage(input_tokens=120, output_tokens=180, wall_seconds=0.01)
        if request.action == WorkAction.PLAN:
            return self._plan(request, usage)
        if request.action == WorkAction.ANALYSE:
            return self._analyse(request, usage)
        if request.action == WorkAction.REVIEW:
            return self._review(request, usage)
        if request.action == WorkAction.REVISE:
            return self._revise(request, usage)
        if request.action == WorkAction.SYNTHESIZE:
            return self._synthesize(request, usage)
        raise ValueError(f"unsupported action {request.action}")

    def _plan(self, request: WorkRequest, usage: Usage) -> ProviderResult:
        requests = []
        if request.worker.role == "Mathematician":
            requests.append(
                RoleRequestPayload(
                    requested_role="OperationsResearcher",
                    reason=(
                        "Fleet decomposition and mixed-integer solver design require dedicated "
                        "operations-research ownership beyond proof review."
                    ),
                    required_capabilities=["fleet optimisation", "mixed-integer programming"],
                    related_tasks=[request.task.id] if request.task else [],
                    proposed_jurisdiction=["operations_research", "optimization_model"],
                    proposed_tools=["workspace"],
                )
            )
        return ProviderResult(role_requests=requests, usage=usage)

    def _analyse(self, request: WorkRequest, usage: Usage) -> ProviderResult:
        if request.task is None:
            raise ValueError("analysis requires a task")
        role = request.worker.role
        analyses: dict[str, _Analysis] = {
            "Physicist": {
                "conclusion": (
                    "Use a coarse battery-energy envelope based on distance, load, stop density, "
                    "and reserve charge; treat it as a feasibility constraint, not a calibrated law."
                ),
                "evidence": [
                    "Dimensional check: route energy must remain energy-per-distance times distance.",
                    "A reserve-state constraint prevents assignments that strand a vehicle.",
                ],
                "assumptions": ["Vehicle class and weather are held fixed in the toy mission."],
                "uncertainty": ["No measured coefficients are supplied for congestion or weather."],
                "confidence": 0.68,
                "next": ["Calibrate energy coefficients on held-out telemetry."],
            },
            "Mathematician": {
                "conclusion": (
                    "Represent assignment as a capacitated time-window optimisation with a hard "
                    "battery reserve and a weighted lateness-plus-energy objective."
                ),
                "evidence": [
                    "Binary vehicle-route assignment variables express exclusivity directly.",
                    "Capacity, time-window, and reserve constraints define a checkable feasible set.",
                ],
                "assumptions": [
                    "Travel-time estimates are fixed during each optimisation horizon."
                ],
                "uncertainty": ["Exact mixed-integer solution time may exceed an online deadline."],
                "confidence": 0.78,
                "next": ["Compare exact and decomposed solutions on fixed toy instances."],
            },
            "DataScientist": {
                "conclusion": (
                    "Historical demand can parameterise a toy benchmark, but it cannot support a "
                    "deployment-effectiveness claim without chronological holdout evaluation."
                ),
                "evidence": [
                    "Demand counts, timestamps, and route outcomes define measurable service metrics.",
                    "A chronological split avoids training on demand that occurs after evaluation.",
                ],
                "assumptions": ["The stored demand sample reflects one operating region."],
                "uncertainty": ["Selection and missingness mechanisms are unknown."],
                "confidence": 0.61,
                "next": ["Create a provenance ledger and frozen chronological test set."],
                "dissent": ["A synthetic demo result is not evidence of real fleet savings."],
            },
            "SoftwareEngineer": {
                "conclusion": (
                    "Build a deterministic greedy baseline behind a narrow solver interface, then "
                    "add the constrained optimiser only if the baseline fails acceptance tests."
                ),
                "evidence": [
                    "A fixed fixture and seed make scheduling regressions reproducible.",
                    "A solver timeout can return the last feasible assignment without hiding failure.",
                ],
                "assumptions": ["The first demo runs in one local process."],
                "uncertainty": ["Production latency and concurrency are not measured."],
                "confidence": 0.84,
                "next": ["Implement one fixture, invariant checks, and a baseline comparison."],
            },
            "RedTeam": {
                "conclusion": (
                    "The mission is demonstrable only if energy coefficients, demand provenance, "
                    "solver timeouts, and the distinction between toy and real evidence stay visible."
                ),
                "evidence": [
                    "A feasible optimiser can still encode a physically invalid energy model.",
                    "Aggregate averages can hide missed time windows and stranded routes.",
                ],
                "assumptions": ["No live operational data is available in this demonstration."],
                "uncertainty": ["Unknown failure modes may emerge under shifted demand."],
                "confidence": 0.74,
                "next": ["Attempt adversarial high-load and sparse-charger scenarios."],
                "dissent": ["Do not label toy improvements as operational savings."],
            },
            "OperationsResearcher": {
                "conclusion": (
                    "Use rolling-horizon decomposition: generate feasible route bundles, assign "
                    "vehicles to bundles, and re-optimise only when new demand crosses a threshold."
                ),
                "evidence": [
                    "Decomposition separates combinatorial route generation from fleet assignment.",
                    "A feasible incumbent provides a bounded fallback when the solver times out.",
                ],
                "assumptions": ["Replanning has a stable minimum interval."],
                "uncertainty": ["The useful replanning threshold requires empirical calibration."],
                "confidence": 0.76,
                "next": ["Measure objective gap and latency against the greedy baseline."],
            },
        }
        if role not in analyses:
            raise ValueError(f"no deterministic analysis for role {role}")
        data = analyses[role]
        report = SpecialistReport(
            worker_id=request.worker.id,
            project_id=request.project_id,
            task_id=request.task.id,
            subject_type=request.task.subject_type,
            question=request.task.description,
            conclusion=data["conclusion"],
            evidence=data["evidence"],
            assumptions=data["assumptions"],
            uncertainty=data["uncertainty"],
            confidence=Confidence(
                score=float(data["confidence"]),
                rationale="Deterministic demo confidence tied to stated evidence and uncertainty.",
            ),
            unresolved_questions=data["uncertainty"],
            recommended_next_steps=data["next"],
            dissent_from_consensus=data.get("dissent", []),
        )
        markdown = (
            f"# {role} independent report\n\n"
            f"## Conclusion\n\n{report.conclusion}\n\n"
            "## Evidence\n\n" + "\n".join(f"- {item}" for item in report.evidence) + "\n"
        )
        return ProviderResult(report=report, artifact_markdown=markdown, usage=usage)

    def _review(self, request: WorkRequest, usage: Usage) -> ProviderResult:
        report = request.report_to_review
        if report is None:
            raise ValueError("review requires a report")
        reviewer = request.worker.role
        owner_role = next(
            (
                message.subject.removeprefix("Review ")
                for message in request.assigned_messages
                if message.subject.startswith("Review ")
            ),
            "specialist",
        )
        verdict = ReviewVerdict.ACCEPT
        findings = [f"{reviewer} checked {report.subject_type} within assigned jurisdiction."]
        objections: list[str] = []
        requested_changes: list[str] = []
        safety = False
        if reviewer == "RedTeam" and report.subject_type == "statistical_claim":
            verdict = ReviewVerdict.DISPUTE
            objections = [
                "Even toy tuning may be misleading until demand missingness and sampling are shown."
            ]
        elif reviewer == "RedTeam" and report.subject_type == "physical_model":
            verdict = ReviewVerdict.CHANGES_REQUESTED
            objections = ["Uncalibrated energy coefficients can create unsafe reserve estimates."]
            requested_changes = [
                "Label coefficients as placeholders and add a conservative margin."
            ]
            safety = True
        elif reviewer == "DataScientist" and report.subject_type == "optimization_model":
            verdict = ReviewVerdict.CHANGES_REQUESTED
            objections = ["The objective weights lack an empirical or sensitivity basis."]
            requested_changes = ["Add a sensitivity sweep before selecting objective weights."]
        review = Review(
            project_id=request.project_id,
            report_id=report.id,
            report_version=report.version,
            reviewer_worker_id=request.worker.id,
            subject_type=report.subject_type,
            verdict=verdict,
            findings=findings,
            objections=objections,
            requested_changes=requested_changes,
            evidence=[
                f"Reviewed immutable report {report.id} version {report.version} by {owner_role}."
            ],
            confidence=Confidence(
                score=0.8 if verdict == ReviewVerdict.ACCEPT else 0.72,
                rationale="Deterministic jurisdiction-focused review.",
            ),
            safety_objection=safety,
        )
        return ProviderResult(review=review, usage=usage)

    def _revise(self, request: WorkRequest, usage: Usage) -> ProviderResult:
        prior = request.prior_report
        if prior is None:
            raise ValueError("revision requires a prior report")
        changes = [change for review in request.reviews for change in review.requested_changes]
        revised = prior.model_copy(
            update={
                "id": new_id("report"),
                "conclusion": prior.conclusion + " Revision: " + " ".join(changes),
                "evidence": [*prior.evidence, *[f"Revision response: {item}" for item in changes]],
                "objections": [
                    *prior.objections,
                    *[item for r in request.reviews for item in r.objections],
                ],
                "status": ReportStatus.REVISED,
                "version": prior.version + 1,
                "supersedes_report_id": prior.id,
                "content_sha256": None,
            }
        )
        markdown = (
            f"# {request.worker.role} revised report v{revised.version}\n\n{revised.conclusion}\n"
        )
        return ProviderResult(report=revised, artifact_markdown=markdown, usage=usage)

    def _synthesize(self, request: WorkRequest, usage: Usage) -> ProviderResult:
        current_reports = request.visible_reports
        artifact_refs = sorted(
            {item for report in current_reports for item in report.artifact_refs}
        )
        report = DirectorReport(
            project_id=request.project_id,
            director_worker_id=request.worker.id,
            consensus=[
                "Use hard feasibility constraints before optimising service objectives.",
                "Benchmark a deterministic baseline before adding solver complexity.",
                "Keep toy evidence separate from claims about operational savings.",
            ],
            disagreements=[
                "Data Science accepts historical demand for a bounded toy benchmark; Red Team says "
                "unknown missingness may make even tuning conclusions unstable.",
                "The physical envelope is useful for feasibility, but its uncalibrated coefficients "
                "cannot yet establish a safe real-world reserve.",
            ],
            evidence=[
                f"Specialist report {report.id} v{report.version}" for report in current_reports
            ],
            assumptions=["This is a synthetic single-region mission with no live fleet telemetry."],
            risks=[
                "Unsafe assignments if placeholder energy coefficients are treated as calibrated.",
                "Overstated value if chronological and shifted-demand tests are omitted.",
            ],
            unresolved_questions=[
                "How missing is the demand history?",
                "What reserve margin is safe across vehicle class and weather?",
            ],
            director_judgement=(
                "Proceed with a transparent toy prototype only. The most valuable next evidence is "
                "a frozen stress-test matrix combining shifted demand and conservative energy bounds."
            ),
            recommended_action=(
                "Run the greedy and rolling-horizon methods on that matrix; report feasibility, "
                "lateness, energy reserve, solver time, and every failed case."
            ),
            artifact_refs=artifact_refs,
            source_report_ids=[report.id for report in current_reports],
            source_review_ids=[review.id for review in request.reviews],
        )
        return ProviderResult(report=None, director_report=report, usage=usage)
