from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass

from .schema import Plan, TaskDomain
from .semantics import execute_plan


class EnumerationLimitExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class EnumerationStats:
    expanded_nodes: int
    generated_nodes: int
    successful_plans: int
    dead_ends: int


@dataclass(frozen=True)
class EnumerationResult:
    plans: tuple[Plan, ...]
    stats: EnumerationStats


@dataclass(frozen=True)
class _Node:
    state: frozenset[str]
    steps: tuple[str, ...]
    uses: tuple[tuple[str, int], ...]

    def use_counter(self) -> Counter[str]:
        return Counter(dict(self.uses))


def enumerate_successful_plans(
    domain: TaskDomain, max_nodes: int = 100_000
) -> EnumerationResult:
    """Enumerate all bounded, goal-reaching action sequences.

    Enumeration stops expanding a sequence as soon as the outcome contract is
    satisfied. The reference space is finite because depth and action-use counts
    are bounded in the benchmark.
    """

    initial = _Node(
        state=domain.initial_facts,
        steps=(),
        uses=(),
    )
    queue: deque[_Node] = deque([initial])
    plans: list[Plan] = []
    expanded = 0
    generated = 1
    dead_ends = 0

    while queue:
        if expanded >= max_nodes:
            raise EnumerationLimitExceeded(
                f"{domain.id}: exceeded max_nodes={max_nodes}; "
                "tighten max_depth/action contracts or raise the explicit limit"
            )
        node = queue.popleft()
        expanded += 1

        candidate = Plan(node.steps, source="reference")
        result = execute_plan(domain, candidate)
        if result.success:
            plans.append(candidate)
            continue
        if len(node.steps) >= domain.max_depth:
            dead_ends += 1
            continue

        uses = node.use_counter()
        applicable = 0
        for action in domain.actions.values():
            if uses[action.id] >= action.max_uses:
                continue
            if not set(action.pre).issubset(node.state):
                continue
            applicable += 1
            next_state = set(node.state)
            next_state.difference_update(action.delete)
            next_state.update(action.add)
            next_uses = uses.copy()
            next_uses[action.id] += 1
            queue.append(
                _Node(
                    state=frozenset(next_state),
                    steps=node.steps + (action.id,),
                    uses=tuple(sorted(next_uses.items())),
                )
            )
            generated += 1
        if applicable == 0:
            dead_ends += 1

    unique: dict[tuple[str, ...], Plan] = {plan.steps: plan for plan in plans}
    # Dict insertion order preserves the deterministic action-registry/BFS
    # order. This matters for pre-registered tie-breaking in baseline runs.
    ordered = tuple(unique.values())
    return EnumerationResult(
        plans=ordered,
        stats=EnumerationStats(
            expanded_nodes=expanded,
            generated_nodes=generated,
            successful_plans=len(ordered),
            dead_ends=dead_ends,
        ),
    )
