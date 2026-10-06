from __future__ import annotations

import hashlib
import json
import random
import subprocess
import time
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping

from .schema import Plan, TaskDomain
from .semantics import (
    analyze_plan, compare_footprints, footprint_to_rows, is_same_result_witness,
)


class LocalModelError(RuntimeError):
    pass


class LocalBackend(ABC):
    @abstractmethod
    def generate(self, prompt: str, seed: int) -> str:
        raise NotImplementedError

    def metadata(self) -> dict[str, Any]:
        return {"backend": type(self).__name__}


class CommandBackend(LocalBackend):
    """Run a user-provided local command that reads the prompt from stdin.

    This supports llama.cpp or another local runtime without tying the
    experiment to a network service. The command is executed directly, never
    through a shell.
    """

    def __init__(self, command: list[str], timeout_seconds: int = 180) -> None:
        if not command:
            raise LocalModelError("local command must not be empty")
        self.command = command
        self.timeout_seconds = timeout_seconds

    def generate(self, prompt: str, seed: int) -> str:
        command = [
            part.replace("{seed}", str(seed)) for part in self.command
        ]
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LocalModelError(f"local model command failed: {exc}") from exc
        if completed.returncode != 0:
            raise LocalModelError(
                f"local model command exited {completed.returncode}: "
                f"{completed.stderr.strip()}"
            )
        return completed.stdout

    def metadata(self) -> dict[str, Any]:
        return {
            "backend": "command",
            "command": self.command,
            "timeout_seconds": self.timeout_seconds,
            "external_api_used": False,
        }


@dataclass(frozen=True)
class CounterplanAttempt:
    """One model call in a result-preserving counterplan descent."""

    call_index: int
    model_calls: int
    seed: int
    phase: str
    raw_output: str
    proposed_plan: Plan | None
    incumbent_before: Plan | None
    incumbent_after: Plan | None
    accepted: bool
    rejection_reason: str | None
    footprint_relation: str | None
    elapsed_seconds: float
    prompt_sha256: str
    removed_observations: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class CounterplanDescentRun:
    """Auditable incumbents after every model call under a fixed budget."""

    call_budget: int
    attempts: tuple[CounterplanAttempt, ...]
    prefix_incumbents: tuple[Plan | None, ...]
    initial_incumbent: Plan | None
    final_incumbent: Plan | None


def _neutral_action_description(description: str) -> str:
    """Remove benchmark-construction labels from model-visible metadata.

    Tau-derived actions append labels such as ``direct broad disclosure`` or
    ``locally refined projection`` after a semicolon.  Those labels disclose
    the benchmark answer.  The grounded operation before the semicolon is the
    neutral task description and is retained.
    """

    return description.split(";", maxsplit=1)[0].strip()


def _tool_view(
    domain: TaskDomain,
    presentation_seed: int,
    action_ids: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return a counterbalanced, opaque presentation of grounded actions."""

    presented = list(domain.actions) if action_ids is None else list(action_ids)
    random.Random(presentation_seed).shuffle(presented)
    alias_to_action = {
        f"A{index + 1}": action_id
        for index, action_id in enumerate(presented)
    }
    result: list[dict[str, Any]] = []
    for alias, action_id in alias_to_action.items():
        action = domain.actions[action_id]
        tool = domain.tools[action.tool]
        item: dict[str, Any] = {
            "action_id": alias,
            "description": _neutral_action_description(action.description),
            "requires": list(action.pre),
            "produces": list(action.add),
            "deletes": list(action.delete),
            "max_uses": action.max_uses,
            "recipient_entity": tool.recipient_entity,
            "recipient_country": tool.recipient_country,
            "processing_country": tool.processing_country,
            "transfer_mode": tool.default_transfer_mode,
            "purpose": tool.purpose,
            "data_accesses": [
                {
                    "atom": access.atom,
                    "personal": domain.data_atoms[access.atom].personal,
                    "mode": access.mode or tool.default_transfer_mode,
                    "purpose": access.purpose or tool.purpose,
                }
                for access in action.data
            ],
        }
        result.append(item)
    return result, alias_to_action


def build_plan_prompt(
    domain: TaskDomain,
    candidate_index: int,
    presentation_seed: int = 0,
) -> tuple[str, dict[str, str], str]:
    tool_view, alias_to_action = _tool_view(domain, presentation_seed)
    payload = {
        "request": domain.request,
        "initial_facts": sorted(domain.initial_facts),
        "required_outcome": {
            "must": list(domain.outcome.must),
            "must_not": list(domain.outcome.must_not),
        },
        "maximum_steps": domain.max_depth,
        "actions": tool_view,
    }
    paired_context_sha256 = hashlib.sha256(
        json.dumps(
            {
                "candidate_index": candidate_index,
                "domain": payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    prompt = (
        "You are planning a deterministic tool-using agent task. "
        "Choose only action_id values from the supplied action list. "
        "Respect preconditions and maximum uses. "
        "Return exactly one JSON object and no prose, with this schema: "
        '{"steps":["action_id", "..."]}.'
        "\n"
        f"Candidate index: {candidate_index}\n"
        f"Domain:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    return prompt, alias_to_action, paired_context_sha256


def _extract_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    # Some local command wrappers echo the prompt, which itself contains a
    # schema example. Prefer the last plan-shaped object emitted by the model.
    plan_objects = [item for item in candidates if "steps" in item]
    if plan_objects:
        return plan_objects[-1]
    if candidates:
        return candidates[-1]
    raise ValueError("no JSON object found")


def parse_generated_plan(
    text: str,
    source: str,
    alias_to_action: dict[str, str] | None = None,
) -> Plan:
    obj = _extract_json_object(text)
    steps = obj.get("steps")
    if not isinstance(steps, list) or not all(
        isinstance(step, str) for step in steps
    ):
        raise ValueError("JSON field 'steps' must be a list of strings")
    if alias_to_action is not None:
        unknown = [step for step in steps if step not in alias_to_action]
        if unknown:
            raise ValueError(
                f"model returned unknown opaque action id(s): {unknown}"
            )
        steps = [alias_to_action[step] for step in steps]
    return Plan(tuple(steps), source=source, raw_output=text)


def _parse_generated_action(
    text: str, alias_to_action: dict[str, str]
) -> str:
    obj = _extract_json_object(text)
    alias = obj.get("action_id")
    if not isinstance(alias, str):
        raise ValueError("JSON field 'action_id' must be a string")
    if alias not in alias_to_action:
        raise ValueError(f"model returned unknown opaque action id: {alias!r}")
    return alias_to_action[alias]


def _goal_reached(domain: TaskDomain, state: set[str]) -> bool:
    return set(domain.outcome.must).issubset(state) and not (
        set(domain.outcome.must_not) & state
    )


def _build_step_prompt(
    domain: TaskDomain,
    candidate_index: int,
    presentation_seed: int,
    state: set[str],
    steps: list[str],
    applicable: list[str],
) -> tuple[str, dict[str, str]]:
    tool_view, alias_to_action = _tool_view(
        domain, presentation_seed, action_ids=applicable
    )
    payload = {
        "request": domain.request,
        "current_facts": sorted(state),
        "completed_action_descriptions": [
            _neutral_action_description(domain.actions[action_id].description)
            for action_id in steps
        ],
        "required_outcome": {
            "must": list(domain.outcome.must),
            "must_not": list(domain.outcome.must_not),
        },
        "remaining_step_budget": domain.max_depth - len(steps),
        "currently_applicable_actions": tool_view,
    }
    prompt = (
        "You are choosing the next grounded action in a stateful agent "
        "workflow. Return exactly one JSON object and no prose, with this "
        'schema: {"action_id":"A1"}. Choose only from the currently '
        "applicable actions.\n"
        f"Candidate index: {candidate_index}\n"
        f"State:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    return prompt, alias_to_action


def _counterplan_observation_rows(
    domain: TaskDomain, plan: Plan
) -> list[dict[str, Any]]:
    """Expose precisely the observer/data coordinates used by set inclusion."""

    rows = footprint_to_rows(analyze_plan(domain, plan).footprint)
    return [
        {
            "data_atom": row["data_atom"],
            "data_subject": row["data_subject"],
            "foreign_observer": row["importer_entity"],
            "observer_country": row["importer_entity_country"],
            "processing_country": row["processing_country"],
        }
        for row in rows
    ]


def build_counterplan_prompt(
    domain: TaskDomain,
    incumbent: Plan,
    call_index: int,
    presentation_seed: int,
    previous_rejection: str | None = None,
    previous_proposal: Plan | None = None,
) -> tuple[str, dict[str, str]]:
    """Present identical evidence while varying the requested improvement."""

    tool_view, alias_to_action = _tool_view(domain, presentation_seed)
    action_to_alias = {
        action_id: alias for alias, action_id in alias_to_action.items()
    }
    incumbent_aliases = [
        action_to_alias[action_id] for action_id in incumbent.steps
    ]
    payload: dict[str, Any] = {
        "request": domain.request,
        "initial_facts": sorted(domain.initial_facts),
        "required_result": {
            "must": list(domain.outcome.must),
            "must_not": list(domain.outcome.must_not),
        },
        "maximum_steps": domain.max_depth,
        "actions": tool_view,
        "incumbent_steps": incumbent_aliases,
        "incumbent_foreign_observations": _counterplan_observation_rows(
            domain, incumbent
        ),
    }
    if previous_rejection is not None:
        payload["previous_rejection"] = previous_rejection
    if previous_proposal is not None:
        payload["previous_proposal_steps"] = [
            action_to_alias[action_id] for action_id in previous_proposal.steps
        ]
    prompt = (
        "You are improving a deterministic tool-using agent plan. The current "
        "plan already achieves the declared result. Return a complete alternative "
        "plan that achieves that same result and whose foreign-observation set is "
        "a strict subset of the incumbent set. An observation records which "
        "foreign observer can see which personal data. Never trade away the "
        "declared result. Choose only action_id values from the supplied list and "
        "return exactly one JSON object and no prose, with this schema: "
        '{"steps":["action_id", "..."]}.\n'
        f"Call index: {call_index}\n"
        f"Problem:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    return prompt, alias_to_action


def _build_counterplan_step_prompt(
    domain: TaskDomain,
    incumbent: Plan,
    proposal_index: int,
    presentation_seed: int,
    state: set[str],
    steps: list[str],
    applicable: list[str],
    previous_rejection: str | None,
    previous_proposal: Plan | None,
) -> tuple[str, dict[str, str]]:
    all_actions, all_aliases = _tool_view(domain, presentation_seed)
    applicable_set = set(applicable)
    visible_actions = [
        item
        for item in all_actions
        if all_aliases[str(item["action_id"])] in applicable_set
    ]
    selectable_aliases = {
        alias: action_id
        for alias, action_id in all_aliases.items()
        if action_id in applicable_set
    }
    action_to_alias = {
        action_id: alias for alias, action_id in all_aliases.items()
    }
    payload: dict[str, Any] = {
        "request": domain.request,
        "current_facts": sorted(state),
        "completed_steps": [action_to_alias[action_id] for action_id in steps],
        "required_result": {
            "must": list(domain.outcome.must),
            "must_not": list(domain.outcome.must_not),
        },
        "remaining_step_budget": domain.max_depth - len(steps),
        "currently_applicable_actions": visible_actions,
        "incumbent_steps": [
            action_to_alias[action_id] for action_id in incumbent.steps
        ],
        "incumbent_foreign_observations": _counterplan_observation_rows(
            domain, incumbent
        ),
    }
    if previous_rejection is not None:
        payload["previous_rejection"] = previous_rejection
    if previous_proposal is not None:
        payload["previous_proposal_steps"] = [
            action_to_alias[action_id] for action_id in previous_proposal.steps
        ]
    prompt = (
        "You are choosing the next grounded action for a complete alternative "
        "to a successful incumbent plan. The alternative must achieve the same "
        "declared result and its foreign-observation set must "
        "be a strict subset of the incumbent set. An observation records which foreign observer can "
        "see which personal data. Return exactly one JSON object and no prose, "
        'with this schema: {"action_id":"A1"}. Choose only from the currently '
        "applicable actions.\n"
        f"Proposal index: {proposal_index}\n"
        f"State:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    return prompt, selectable_aliases


def _stepwise_counterplan_proposal(
    domain: TaskDomain,
    backend: LocalBackend,
    proposal_index: int,
    candidate_seed: int,
    max_model_calls: int,
    incumbent: Plan | None,
    previous_rejection: str | None,
    previous_proposal: Plan | None,
) -> tuple[Plan | None, str, int, tuple[str, ...], str | None]:
    state = set(domain.initial_facts)
    uses: Counter[str] = Counter()
    steps: list[str] = []
    decisions: list[dict[str, Any]] = []
    prompt_hashes: list[str] = []
    model_calls = 0
    error: str | None = None
    try:
        while not _goal_reached(domain, state):
            if len(steps) >= domain.max_depth:
                raise ValueError("stepwise rollout exhausted max_depth")
            applicable = [
                action.id
                for action in domain.actions.values()
                if uses[action.id] < action.max_uses
                and set(action.pre).issubset(state)
            ]
            if not applicable:
                raise ValueError(
                    "stepwise rollout reached a state with no applicable action"
                )
            step_seed = candidate_seed + len(steps) * 104729
            if len(applicable) == 1:
                action_id = applicable[0]
                decisions.append(
                    {
                        "step": len(steps),
                        "selected": action_id,
                        "selection": "forced",
                        "applicable": applicable,
                    }
                )
            else:
                if model_calls >= max_model_calls:
                    raise ValueError("model_call_budget_exhausted")
                if incumbent is None:
                    prompt, alias_to_action = _build_step_prompt(
                        domain,
                        proposal_index,
                        step_seed,
                        state,
                        steps,
                        applicable,
                    )
                else:
                    prompt, alias_to_action = _build_counterplan_step_prompt(
                        domain,
                        incumbent,
                        proposal_index,
                        step_seed,
                        state,
                        steps,
                        applicable,
                        previous_rejection,
                        previous_proposal,
                    )
                prompt_hashes.append(
                    hashlib.sha256(prompt.encode("utf-8")).hexdigest()
                )
                model_calls += 1
                raw = backend.generate(prompt, step_seed)
                action_id = _parse_generated_action(raw, alias_to_action)
                decisions.append(
                    {
                        "step": len(steps),
                        "selected": action_id,
                        "selection": "model",
                        "applicable": applicable,
                        "raw_output": raw,
                    }
                )
            action = domain.actions[action_id]
            uses[action_id] += 1
            state.difference_update(action.delete)
            state.update(action.add)
            steps.append(action_id)
        plan = Plan(
            tuple(steps),
            source=(
                "local_model:counterplan_descent:stepwise:incumbent_search"
                if incumbent is None
                else "local_model:counterplan_descent:stepwise:strict_descent"
            ),
        )
    except (LocalModelError, ValueError) as exc:
        plan = Plan(tuple(steps), source="local_model:incomplete")
        error = str(exc)
    raw_output = json.dumps(
        {"steps": steps, "decisions": decisions},
        ensure_ascii=False,
    )
    return plan, raw_output, model_calls, tuple(prompt_hashes), error


def generate_counterplan_descent(
    domain: TaskDomain,
    backend: LocalBackend,
    call_budget: int,
    seed: int,
    generation_mode: str = "plan",
    verified_results: Mapping[tuple[str, ...], str] | None = None,
) -> CounterplanDescentRun:
    """Search for same-result strict-subset witnesses under a model-call budget.

    The first successful verified proposal becomes the incumbent. Each later
    proposal must preserve its result and strictly reduce its observation set.
    With a result registry, unknown plans are rejected. Without a registry,
    result equality uses the single symbolic outcome contract of the task.
    """

    if call_budget < 1:
        raise ValueError("call_budget must be at least 1")
    if generation_mode not in {"plan", "stepwise"}:
        raise ValueError("generation_mode must be 'plan' or 'stepwise'")
    randomizer = random.Random(seed)
    attempts: list[CounterplanAttempt] = []
    prefixes: list[Plan | None] = []
    incumbent: Plan | None = None
    initial_incumbent: Plan | None = None
    previous_rejection: str | None = None
    previous_proposal: Plan | None = None

    model_calls_used = 0
    proposal_index = 0
    while model_calls_used < call_budget:
        candidate_seed = randomizer.randrange(0, 2**31)
        incumbent_before = incumbent
        phase = "incumbent_search" if incumbent is None else "strict_descent"
        raw = ""
        proposed_plan: Plan | None = None
        relation: str | None = None
        accepted = False
        rejection_reason: str | None = None
        started = time.perf_counter()
        prompt_hashes: tuple[str, ...] = ()
        model_calls = 0
        rollout_error: str | None = None
        if generation_mode == "stepwise":
            (
                proposed_plan,
                raw,
                model_calls,
                prompt_hashes,
                rollout_error,
            ) = _stepwise_counterplan_proposal(
                domain,
                backend,
                proposal_index=proposal_index,
                candidate_seed=candidate_seed,
                max_model_calls=call_budget - model_calls_used,
                incumbent=incumbent,
                previous_rejection=previous_rejection,
                previous_proposal=previous_proposal,
            )
        else:
            try:
                if incumbent is None:
                    prompt, alias_to_action, _ = build_plan_prompt(
                        domain,
                        candidate_index=proposal_index,
                        presentation_seed=candidate_seed,
                    )
                else:
                    prompt, alias_to_action = build_counterplan_prompt(
                        domain,
                        incumbent,
                        call_index=proposal_index,
                        presentation_seed=candidate_seed,
                        previous_rejection=previous_rejection,
                        previous_proposal=previous_proposal,
                    )
                prompt_hashes = (
                    hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                )
                model_calls = 1
                raw = backend.generate(prompt, candidate_seed)
                proposed_plan = parse_generated_plan(
                    raw,
                    source=f"local_model:counterplan_descent:{phase}",
                    alias_to_action=alias_to_action,
                )
            except (LocalModelError, ValueError) as exc:
                rollout_error = str(exc)

        model_calls_used += model_calls
        try:
            if rollout_error is not None or proposed_plan is None:
                raise ValueError(rollout_error or "proposal did not produce a plan")
            proposed = analyze_plan(domain, proposed_plan)
            if not proposed.execution.feasible or not proposed.execution.success:
                rejection_reason = "failed"
            elif verified_results is not None and proposed_plan.steps not in verified_results:
                rejection_reason = "unverified_result"
            elif incumbent is None:
                incumbent = proposed_plan
                initial_incumbent = proposed_plan
                accepted = True
                previous_rejection = None
                previous_proposal = None
            else:
                current = analyze_plan(domain, incumbent)
                relation = compare_footprints(
                    proposed.footprint, current.footprint
                )
                same_result = (
                    verified_results is None
                    or verified_results[proposed_plan.steps] == verified_results[incumbent.steps]
                )
                improved = is_same_result_witness(current, proposed, verified_results)
                if not same_result:
                    rejection_reason = "result_changed"
                elif improved:
                    incumbent = proposed_plan
                    accepted = True
                    previous_rejection = None
                    previous_proposal = None
                else:
                    rejection_reason = {
                        "equal": "equal",
                        "incomparable": "incomparable",
                        "greater": "not_subset",
                    }[relation]
        except (LocalModelError, ValueError) as exc:
            rejection_reason = "failed"
            raw = raw or str(exc)

        if not accepted:
            previous_rejection = rejection_reason
            previous_proposal = proposed_plan
        removed = ()
        if accepted and incumbent_before is not None and incumbent is not None:
            removed = tuple(footprint_to_rows(
                analyze_plan(domain, incumbent_before).footprint
                - analyze_plan(domain, incumbent).footprint
            ))
        attempts.append(
            CounterplanAttempt(
                call_index=model_calls_used - model_calls,
                model_calls=model_calls,
                seed=candidate_seed,
                phase=phase,
                raw_output=raw,
                proposed_plan=proposed_plan,
                incumbent_before=incumbent_before,
                incumbent_after=incumbent,
                accepted=accepted,
                rejection_reason=rejection_reason,
                footprint_relation=relation,
                elapsed_seconds=time.perf_counter() - started,
                removed_observations=removed,
                prompt_sha256=hashlib.sha256(
                    "\n".join(prompt_hashes).encode("utf-8")
                ).hexdigest(),
            )
        )
        if model_calls:
            prefixes.extend([incumbent_before] * (model_calls - 1))
            prefixes.append(incumbent)
        else:
            # A fully forced rollout needs no model decision.  Its incumbent is
            # therefore available at every remaining budget prefix.
            prefixes.extend([incumbent] * (call_budget - len(prefixes)))
            break
        proposal_index += 1

    return CounterplanDescentRun(
        call_budget=call_budget,
        attempts=tuple(attempts),
        prefix_incumbents=tuple(prefixes),
        initial_incumbent=initial_incumbent,
        final_incumbent=incumbent,
    )
