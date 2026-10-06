from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Iterable, Mapping

from .schema import Action, Plan, TaskDomain

SEMANTICS_VERSION = "jor-observer-data-set-v2"


@dataclass(frozen=True, order=True)
class TransferEvent:
    """Canonical foreign observer–data relationship.

    Step/call indices are deliberately excluded.  The plan footprint is a set,
    so repeating an identical disclosure does not create a new observation
    capability.  Repeated occurrences remain available in ``occurrences`` for
    audit and secondary frequency analyses.
    """

    data_atom: str
    data_subject: str
    # J records who abroad can observe which personal data.  Export mode,
    # purpose, and the (task-constant) exporter remain audit metadata, but they
    # do not split one observer/data pair into several distinct observations.
    exporter_entity: str = field(compare=False)
    importer_entity: str
    importer_entity_country: str
    processing_country: str
    transfer_mode: str = field(compare=False)
    purpose: str = field(compare=False)


Footprint = frozenset[TransferEvent]


@dataclass(frozen=True)
class EventOccurrence:
    event: TransferEvent
    step_index: int
    action_id: str
    foreign_triggers: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionResult:
    feasible: bool
    success: bool
    final_state: frozenset[str]
    error: str | None
    executed_step_count: int


@dataclass(frozen=True)
class PlanAnalysis:
    plan: Plan
    execution: ExecutionResult
    footprint: Footprint
    occurrences: tuple[EventOccurrence, ...]


def is_foreign_destination(domain: TaskDomain, action: Action) -> bool:
    tool = domain.tools[action.tool]
    home = domain.controller.country
    return tool.recipient_country != home or tool.processing_country != home


def execute_plan(domain: TaskDomain, plan: Plan) -> ExecutionResult:
    state = set(domain.initial_facts)
    uses: Counter[str] = Counter()

    if len(plan.steps) > domain.max_depth:
        return ExecutionResult(
            feasible=False,
            success=False,
            final_state=frozenset(state),
            error=(
                f"plan length {len(plan.steps)} exceeds "
                f"max_depth={domain.max_depth}"
            ),
            executed_step_count=0,
        )

    for step_index, action_id in enumerate(plan.steps):
        if set(domain.outcome.must).issubset(state) and not (
            set(domain.outcome.must_not) & state
        ):
            return ExecutionResult(
                feasible=False,
                success=False,
                final_state=frozenset(state),
                error=(
                    f"step {step_index}: action {action_id!r} occurs after "
                    "the task reached its terminal outcome"
                ),
                executed_step_count=step_index,
            )
        action = domain.actions.get(action_id)
        if action is None:
            return ExecutionResult(
                feasible=False,
                success=False,
                final_state=frozenset(state),
                error=f"step {step_index}: unknown action {action_id!r}",
                executed_step_count=step_index,
            )
        uses[action_id] += 1
        if uses[action_id] > action.max_uses:
            return ExecutionResult(
                feasible=False,
                success=False,
                final_state=frozenset(state),
                error=(
                    f"step {step_index}: action {action_id!r} exceeds "
                    f"max_uses={action.max_uses}"
                ),
                executed_step_count=step_index,
            )
        missing = set(action.pre) - state
        if missing:
            return ExecutionResult(
                feasible=False,
                success=False,
                final_state=frozenset(state),
                error=(
                    f"step {step_index}: action {action_id!r} missing "
                    f"preconditions {sorted(missing)}"
                ),
                executed_step_count=step_index,
            )
        state.difference_update(action.delete)
        state.update(action.add)

    success = set(domain.outcome.must).issubset(state) and not (
        set(domain.outcome.must_not) & state
    )
    error = None
    if not success:
        missing_goals = set(domain.outcome.must) - state
        forbidden = set(domain.outcome.must_not) & state
        details: list[str] = []
        if missing_goals:
            details.append(f"missing goals {sorted(missing_goals)}")
        if forbidden:
            details.append(f"forbidden facts {sorted(forbidden)}")
        error = "; ".join(details)
    return ExecutionResult(
        feasible=True,
        success=success,
        final_state=frozenset(state),
        error=error,
        executed_step_count=len(plan.steps),
    )


def extract_transfer_events(
    domain: TaskDomain,
    plan: Plan,
    executed_step_count: int | None = None,
) -> tuple[Footprint, tuple[EventOccurrence, ...]]:
    observations: set[TransferEvent] = set()
    occurrences: list[EventOccurrence] = []

    steps = (
        plan.steps
        if executed_step_count is None
        else plan.steps[:executed_step_count]
    )
    for step_index, action_id in enumerate(steps):
        action = domain.actions.get(action_id)
        if action is None:
            continue
        tool = domain.tools[action.tool]
        foreign = is_foreign_destination(domain, action)
        if not foreign:
            continue
        for access in action.data:
            atom = domain.data_atoms[access.atom]
            if not atom.personal:
                continue
            transfer_mode = access.mode or tool.default_transfer_mode
            event = TransferEvent(
                data_atom=atom.id,
                data_subject=atom.subject,
                exporter_entity=domain.controller.entity,
                importer_entity=tool.recipient_entity,
                importer_entity_country=tool.recipient_country,
                processing_country=tool.processing_country,
                transfer_mode=transfer_mode,
                purpose=access.purpose or tool.purpose,
            )
            observations.add(event)
            triggers: list[str] = []
            if tool.recipient_country != domain.controller.country:
                triggers.append("recipient_country")
            if tool.processing_country != domain.controller.country:
                triggers.append("processing_country")
            occurrences.append(
                EventOccurrence(
                    event=event,
                    step_index=step_index,
                    action_id=action_id,
                    foreign_triggers=tuple(triggers),
                )
            )
    return frozenset(observations), tuple(occurrences)


def analyze_plan(domain: TaskDomain, plan: Plan) -> PlanAnalysis:
    execution = execute_plan(domain, plan)
    footprint, occurrences = extract_transfer_events(
        domain,
        plan,
        executed_step_count=execution.executed_step_count,
    )
    return PlanAnalysis(
        plan=plan,
        execution=execution,
        footprint=footprint,
        occurrences=occurrences,
    )


def footprint_leq(left: Footprint, right: Footprint) -> bool:
    """Set inclusion over canonical foreign observer–data relationships."""

    return left.issubset(right)


def strictly_dominates(left: Footprint, right: Footprint) -> bool:
    return left != right and footprint_leq(left, right)


def compare_footprints(left: Footprint, right: Footprint) -> str:
    """Return less/equal/greater/incomparable under exact set inclusion."""

    if left == right:
        return "equal"
    if footprint_leq(left, right):
        return "less"
    if footprint_leq(right, left):
        return "greater"
    return "incomparable"


def jurisdictional_frontier(
    analyses: Iterable[PlanAnalysis],
    verified_results: Mapping[tuple[str, ...], str] | None = None,
) -> tuple[PlanAnalysis, ...]:
    """Retain every successful plan without a same-result strict-subset witness.

    A supplied registry partitions plans by verified result signature and
    excludes plans absent from it. With no registry, all successful plans must
    belong to one symbolic outcome class in one TaskDomain.
    """
    eligible = [
        item
        for item in analyses
        if item.execution.feasible
        and item.execution.success
        and (verified_results is None or item.plan.steps in verified_results)
    ]
    return tuple(
        item
        for item in eligible
        if not any(
            is_same_result_witness(item, other, verified_results)
            for other in eligible
        )
    )


def find_dominating_witness(
    incumbent: PlanAnalysis,
    analyses: Iterable[PlanAnalysis],
    verified_results: Mapping[tuple[str, ...], str] | None = None,
) -> PlanAnalysis | None:
    witnesses = [
        candidate
        for candidate in analyses
        if is_same_result_witness(incumbent, candidate, verified_results)
    ]
    if not witnesses:
        return None
    return min(
        witnesses,
        key=lambda item: (
            len(item.footprint),
            len(item.plan.steps),
            item.plan.id,
        ),
    )


def is_same_result_witness(
    incumbent: PlanAnalysis,
    candidate: PlanAnalysis,
    verified_results: Mapping[tuple[str, ...], str] | None = None,
) -> bool:
    """Test Result(candidate) == Result(incumbent) and J(candidate) < J(incumbent).

    Analyses must come from the same TaskDomain. The optional trusted registry
    supplies executed result signatures; its missing entries fail closed.
    """
    if not all((incumbent.execution.feasible, incumbent.execution.success,
                candidate.execution.feasible, candidate.execution.success)):
        return False
    if verified_results is not None:
        if incumbent.plan.steps not in verified_results or candidate.plan.steps not in verified_results:
            return False
        if verified_results[incumbent.plan.steps] != verified_results[candidate.plan.steps]:
            return False
    return strictly_dominates(candidate.footprint, incumbent.footprint)


def footprint_to_rows(footprint: Footprint) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for event in sorted(footprint):
        row = asdict(event)
        rows.append(row)
    return rows


def footprint_signature(footprint: Footprint) -> tuple[TransferEvent, ...]:
    return tuple(sorted(footprint))
