"""Data-driven authority checks."""

from __future__ import annotations

from .models import (
    Constitution,
    DirectorReport,
    ProjectState,
    ReportStatus,
    ReviewVerdict,
    RoleRequestRecord,
    RoleTemplate,
    Worker,
)


class GovernanceError(ValueError):
    pass


def default_constitution() -> Constitution:
    templates = {
        "ResearchDirector": RoleTemplate(
            role="ResearchDirector",
            expertise=["mission decomposition", "evidence synthesis", "resource allocation"],
            jurisdiction=["organisation", "director_judgement", "project_completion"],
            owns=["organisation", "director_judgement", "project_completion"],
            must_review=["safety_objection"],
            cannot_approve=["physical_validity", "statistical_claim"],
        ),
        "Physicist": RoleTemplate(
            role="Physicist",
            expertise=["physical modelling", "dimensional analysis", "energy systems"],
            jurisdiction=["physical_model", "physical_validity", "simulation_model"],
            owns=["physical_model", "physical_assumptions", "dimensional_consistency"],
            must_review=["implementation_feasibility"],
            cannot_approve=["statistical_claim"],
            allowed_tools=["workspace"],
        ),
        "Mathematician": RoleTemplate(
            role="Mathematician",
            expertise=["optimisation", "proof", "constraint modelling"],
            jurisdiction=["optimization_model", "mathematical_validity"],
            owns=["optimization_model", "mathematical_assumptions"],
            must_review=["physical_model"],
            cannot_approve=["statistical_claim"],
            allowed_tools=["workspace"],
        ),
        "DataScientist": RoleTemplate(
            role="DataScientist",
            expertise=["empirical validation", "statistics", "data provenance"],
            jurisdiction=["statistical_claim", "empirical_claim", "data_quality"],
            owns=["statistical_claim", "data_quality", "evaluation_design"],
            must_review=["optimization_model"],
            cannot_approve=["physical_validity"],
            allowed_tools=["workspace"],
        ),
        "SoftwareEngineer": RoleTemplate(
            role="SoftwareEngineer",
            expertise=["implementation", "testing", "operational reliability"],
            jurisdiction=["implementation_feasibility", "software_design", "tool_execution"],
            owns=["implementation_feasibility", "software_design"],
            must_review=["optimization_model"],
            cannot_approve=["statistical_claim", "physical_validity"],
            allowed_tools=["workspace"],
        ),
        "RedTeam": RoleTemplate(
            role="RedTeam",
            expertise=["adversarial review", "assumption testing", "failure analysis"],
            jurisdiction=["adversarial_analysis", "all_reports", "safety_objection"],
            owns=["adversarial_analysis", "safety_objection"],
            must_review=["all_reports"],
            cannot_approve=["physical_validity", "statistical_claim"],
            allowed_tools=["workspace"],
        ),
        "OperationsResearcher": RoleTemplate(
            role="OperationsResearcher",
            expertise=["fleet optimisation", "mixed-integer programming", "decomposition"],
            jurisdiction=["operations_research", "optimization_model"],
            owns=["operations_research", "solver_strategy"],
            must_review=["implementation_feasibility"],
            cannot_approve=["statistical_claim", "physical_validity"],
            allowed_tools=["workspace"],
        ),
    }
    return Constitution(
        decision_owners={
            "organisation": ["ResearchDirector"],
            "project_completion": ["ResearchDirector"],
            "physical_validity": ["Physicist"],
            "statistical_claim": ["DataScientist"],
            "implementation_feasibility": ["SoftwareEngineer"],
        },
        mandatory_reviewers={
            "physical_model": ["Mathematician", "RedTeam"],
            "optimization_model": ["DataScientist", "SoftwareEngineer", "RedTeam"],
            "statistical_claim": ["Mathematician", "RedTeam"],
            "implementation_feasibility": ["Physicist", "OperationsResearcher", "RedTeam"],
            "adversarial_analysis": ["ResearchDirector"],
            "operations_research": ["Mathematician", "SoftwareEngineer", "RedTeam"],
        },
        forbidden_approvers={
            "statistical_claim": ["SoftwareEngineer", "Physicist", "RedTeam"],
            "physical_validity": ["DataScientist", "SoftwareEngineer", "RedTeam"],
        },
        role_creators=["ResearchDirector"],
        allowed_role_templates=templates,
        max_workers=8,
        minimum_evidence_items=1,
        maximum_review_rounds=2,
    )


class GovernancePolicy:
    def __init__(self, constitution: Constitution):
        self.constitution = constitution

    def require_jurisdiction(self, worker: Worker, subject: str) -> None:
        if subject not in {*worker.jurisdiction, *worker.owns}:
            raise GovernanceError(f"{worker.role} has no jurisdiction over {subject}")

    def require_approval_authority(self, worker: Worker, subject: str) -> None:
        forbidden = set(worker.cannot_approve)
        forbidden.update(self.constitution.forbidden_approvers.get(subject, []))
        if subject in forbidden or worker.role in forbidden:
            raise GovernanceError(f"{worker.role} cannot approve {subject}")
        owners = self.constitution.decision_owners.get(subject)
        if owners and worker.role not in owners:
            raise GovernanceError(f"{worker.role} does not own approval of {subject}")

    def required_reviewer_roles(self, subject: str) -> list[str]:
        return self.constitution.mandatory_reviewers.get(subject, ["RedTeam"])

    def require_role_creation(
        self,
        actor: Worker,
        role_request: RoleRequestRecord,
        project: ProjectState,
    ) -> RoleTemplate:
        if actor.role not in self.constitution.role_creators:
            raise GovernanceError(f"{actor.role} cannot create roles")
        if role_request.status.value != "PENDING":
            raise GovernanceError("role request has already been decided")
        requested = role_request.request.requested_role
        template = self.constitution.allowed_role_templates.get(requested)
        if template is None:
            raise GovernanceError(f"role template {requested!r} is not allowed")
        if len(project.workers) >= self.constitution.max_workers:
            raise GovernanceError("organisation worker limit reached")
        if any(worker.role == requested for worker in project.workers.values()):
            raise GovernanceError(f"role {requested!r} already exists")
        available = {*template.expertise, *template.jurisdiction, *template.owns}
        missing = set(role_request.request.required_capabilities) - available
        if missing:
            raise GovernanceError(f"role template lacks requested capabilities: {sorted(missing)}")
        if not set(role_request.request.related_tasks) <= set(project.tasks):
            raise GovernanceError("role request references an unknown task")
        if not set(role_request.request.proposed_jurisdiction) <= set(template.jurisdiction):
            raise GovernanceError("role request exceeds the template jurisdiction")
        if not set(role_request.request.proposed_tools) <= set(template.allowed_tools):
            raise GovernanceError("role request asks for tools outside the template allowance")
        return template

    def validate_report_evidence(self, evidence: list[str]) -> None:
        if len(evidence) < self.constitution.minimum_evidence_items:
            raise GovernanceError("report does not meet the minimum evidence requirement")

    def validate_director_report(self, project: ProjectState, report: DirectorReport) -> None:
        report_ids = set(project.reports)
        review_ids = set(project.reviews)
        if not set(report.source_report_ids) <= report_ids:
            raise GovernanceError("director report cites an unknown specialist report")
        if not set(report.source_review_ids) <= review_ids:
            raise GovernanceError("director report cites an unknown review")
        if project.barrier:
            required_reports = {
                max(
                    (item for item in project.reports.values() if item.worker_id == worker_id),
                    key=lambda item: item.version,
                ).id
                for worker_id in project.barrier.required_worker_ids
            }
            if not required_reports <= set(report.source_report_ids):
                raise GovernanceError("director report omitted current specialist work")
        if review_ids and not review_ids <= set(report.source_review_ids):
            raise GovernanceError("director report omitted peer-review evidence")
        safety_reviews = {
            review.id for review in project.reviews.values() if review.safety_objection
        }
        if not safety_reviews <= set(report.source_review_ids):
            raise GovernanceError("director report silently omitted a safety objection")
        if safety_reviews and not report.disagreements:
            raise GovernanceError("director report erased unresolved safety dissent")
        disputed = any(item.status == ReportStatus.DISPUTED for item in project.reports.values()) or any(
            item.verdict == ReviewVerdict.DISPUTE for item in project.reviews.values()
        )
        if disputed and not any(item.strip() for item in report.disagreements):
            raise GovernanceError("director report erased explicit disputed findings")
        if not set(report.artifact_refs) <= set(project.artifacts):
            raise GovernanceError("director report invented artifact references")

    def validate_review_coverage(self, project: ProjectState) -> None:
        if project.barrier is None:
            raise GovernanceError("review coverage requires a report barrier")
        role_by_worker = {worker.id: worker.role for worker in project.workers.values()}
        for report_id in project.barrier.committed_report_ids.values():
            report = project.reports[report_id]
            owner_role = role_by_worker[report.worker_id]
            expected = set(self.required_reviewer_roles(report.subject_type)) - {owner_role}
            actual = {
                role_by_worker[review.reviewer_worker_id]
                for review in project.reviews.values()
                if review.report_id == report.id
            }
            if missing := expected - actual:
                raise GovernanceError(
                    f"report {report.id} is missing mandatory reviewers: {sorted(missing)}"
                )
