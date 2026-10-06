from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest

from bordernec import LocalBackend, Plan, analyze_plan, generate_counterplan_descent, load_benchmark
from bordernec.cli import ScriptedDemoBackend
from bordernec.local_model import LocalModelError, build_counterplan_prompt, parse_generated_plan
from bordernec.schema import Action, OutcomeContract

ROOT = Path(__file__).resolve().parents[1]
TASKS = {task.id: task for task in load_benchmark(ROOT / "examples/pilot_12.json").tasks}


class FixedBackend(LocalBackend):
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = iter(outputs)
        self.calls = 0

    def generate(self, prompt: str, seed: int) -> str:
        self.calls += 1
        return next(self.outputs)


class WidthBackend(LocalBackend):
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str, seed: int) -> str:
        self.prompts.append(prompt)
        marker = next(marker for marker in ("Domain:\n", "Problem:\n", "State:\n") if marker in prompt)
        payload = json.loads(prompt.split(marker, 1)[1])
        actions = payload.get("actions", payload.get("currently_applicable_actions"))
        narrow = "incumbent_foreign_observations" in payload
        selected = min(actions, key=lambda action: len(action["data_accesses"]) * (1 if narrow else -1))
        key = "action_id" if "currently_applicable_actions" in payload else "steps"
        value = selected["action_id"] if key == "action_id" else [selected["action_id"]]
        return json.dumps({key: value})


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = TASKS["B02_hotel_search_oversharing"]

    def test_scripted_acceptance_and_audit_certificate(self) -> None:
        run = generate_counterplan_descent(self.task, ScriptedDemoBackend(self.task), 5, 19)
        self.assertEqual([a.accepted for a in run.attempts], [True, False, True, False, False])
        self.assertEqual([a.rejection_reason for a in run.attempts], [None, "failed", None, "not_subset", "equal"])
        self.assertEqual(run.final_incumbent.steps, ("search_minimal",))
        self.assertEqual({row["data_atom"] for row in run.attempts[2].removed_observations}, {"name", "email"})
        self.assertTrue(all(not attempt.removed_observations for i, attempt in enumerate(run.attempts) if i != 2))

    def test_preserves_incumbent_and_never_exceeds_actual_call_budget(self) -> None:
        for mode in ("plan", "stepwise"):
            backend = WidthBackend()
            run = generate_counterplan_descent(self.task, backend, 4, 19, generation_mode=mode)
            self.assertEqual(sum(a.model_calls for a in run.attempts), 4)
            self.assertEqual(len(backend.prompts), 4)
            self.assertEqual(len(run.prefix_incumbents), 4)
            self.assertEqual(run.final_incumbent.steps, ("search_minimal",))
            self.assertIn("previous_rejection", backend.prompts[-1])

    def test_external_signatures_reject_different_result_and_unknown_plan(self) -> None:
        broad, narrow = ("search_with_identity",), ("search_minimal",)
        for registry, rejection in (({broad: "A", narrow: "B"}, "result_changed"),
                                    ({broad: "A"}, "unverified_result")):
            run = generate_counterplan_descent(self.task, WidthBackend(), 2, 19, verified_results=registry)
            self.assertEqual(run.attempts[1].rejection_reason, rejection)
            self.assertEqual(run.final_incumbent.steps, broad)
        empty = generate_counterplan_descent(self.task, WidthBackend(), 2, 19, verified_results={})
        self.assertIsNone(empty.final_incumbent)
        self.assertEqual(len(empty.attempts), 2)

    def test_smaller_changed_recipient_is_rejected(self) -> None:
        narrow = self.task.actions["search_minimal"]
        tools = dict(self.task.tools)
        tools["alternative"] = replace(tools[narrow.tool], id="alternative", recipient_entity="other_provider")
        actions = dict(self.task.actions)
        name = next(access for access in actions["search_with_identity"].data if access.atom == "name")
        actions["search_minimal"] = replace(narrow, tool="alternative", data=(name,), required_data=("name",))
        task = replace(self.task, actions=actions, tools=tools)
        run = generate_counterplan_descent(task, WidthBackend(), 2, 19)
        self.assertEqual(run.attempts[1].footprint_relation, "incomparable")
        self.assertFalse(run.attempts[1].accepted)

    def test_invalid_model_output_and_backend_error_consume_budget(self) -> None:
        run = generate_counterplan_descent(self.task, FixedBackend(["not json", '{"steps":["unknown"]}']), 2, 19)
        self.assertIsNone(run.final_incumbent)
        self.assertEqual(sum(a.model_calls for a in run.attempts), 2)
        class Broken(LocalBackend):
            def generate(self, prompt: str, seed: int) -> str:
                raise LocalModelError("unavailable")
        self.assertEqual(len(generate_counterplan_descent(self.task, Broken(), 2, 19).attempts), 2)

    def test_seeded_reproducibility(self) -> None:
        left = generate_counterplan_descent(self.task, ScriptedDemoBackend(self.task), 5, 19)
        right = generate_counterplan_descent(self.task, ScriptedDemoBackend(self.task), 5, 19)
        for a, b in zip(left.attempts, right.attempts):
            self.assertEqual((a.seed, a.prompt_sha256, a.raw_output, a.accepted),
                             (b.seed, b.prompt_sha256, b.raw_output, b.accepted))

    def test_alias_parser_uses_last_plan_and_rejects_unknown_alias(self) -> None:
        aliases = {"A1": "broad", "A2": "narrow"}
        plan = parse_generated_plan('echo {"steps":["A1"]} answer {"steps":["A2"]}', "test", aliases)
        self.assertEqual(plan.steps, ("narrow",))
        with self.assertRaises(ValueError):
            parse_generated_plan('{"steps":["A3"]}', "test", aliases)

    def test_forced_steps_do_not_spend_model_calls(self) -> None:
        task = TASKS["B06_necessary_japanese_booking"]
        task = replace(task, actions={"book_with_jp_provider": task.actions["book_with_jp_provider"]})
        backend = FixedBackend([])
        run = generate_counterplan_descent(task, backend, 3, 19, generation_mode="stepwise")
        self.assertEqual(backend.calls, 0)
        self.assertEqual(sum(a.model_calls for a in run.attempts), 0)
        self.assertEqual(len(run.prefix_incumbents), 3)
        self.assertTrue(analyze_plan(task, run.final_incumbent).execution.success)

    def test_incomplete_stepwise_proposal_is_retained_for_feedback(self) -> None:
        task = TASKS["B12_refund_outcome_contract"]
        actions = dict(task.actions)
        actions["mark_local_only"] = replace(actions["mark_local_only"], delete=tuple(task.initial_facts))
        task = replace(task, actions=actions)
        class FailedPath(LocalBackend):
            def generate(self, prompt: str, seed: int) -> str:
                payload = json.loads(prompt.split("State:\n", 1)[1])
                description = task.actions["mark_local_only"].description.split(";", 1)[0].strip()
                selected = next(action for action in payload["currently_applicable_actions"]
                                if action["description"] == description)
                return json.dumps({"action_id": selected["action_id"]})
        run = generate_counterplan_descent(task, FailedPath(), 2, 19, generation_mode="stepwise")
        self.assertIsNone(run.final_incumbent)
        self.assertEqual(run.attempts[0].proposed_plan.steps, ("mark_local_only",))
        self.assertEqual(run.attempts[0].rejection_reason, "failed")

    def test_multiple_decisions_share_one_total_budget(self) -> None:
        tool = next(iter(self.task.tools))
        actions = {
            key: Action(key, key, tool, pre, add)
            for key, pre, add in (
                ("start_a", ("ready",), ("middle",)),
                ("start_b", ("ready",), ("middle",)),
                ("finish_a", ("middle",), ("done",)),
                ("finish_b", ("middle",), ("done",)),
            )
        }
        # Remove the first-stage actions from applicability at the second stage.
        actions = {key: replace(action, delete=("ready",) if key.startswith("start") else ())
                   for key, action in actions.items()}
        task = replace(self.task, initial_facts=frozenset({"ready"}), actions=actions,
                       outcome=OutcomeContract(("done",)), max_depth=2)
        incomplete = generate_counterplan_descent(task, FixedBackend(['{"action_id":"A1"}']),
                                                  1, 19, generation_mode="stepwise")
        self.assertIsNone(incomplete.final_incumbent)
        self.assertEqual(sum(a.model_calls for a in incomplete.attempts), 1)
        complete = generate_counterplan_descent(task, FixedBackend(['{"action_id":"A1"}'] * 2),
                                                2, 19, generation_mode="stepwise")
        self.assertEqual(sum(a.model_calls for a in complete.attempts), 2)
        self.assertEqual(complete.prefix_incumbents[0], None)
        self.assertEqual(len(complete.final_incumbent.steps), 2)

    def test_prompt_contains_incumbent_and_omits_fixture_labels(self) -> None:
        prompt, _ = build_counterplan_prompt(self.task, Plan(("search_with_identity",)), 1, 19,
                                            previous_rejection="equal")
        for field in ("incumbent_steps", "incumbent_foreign_observations", "previous_rejection"):
            self.assertIn(field, prompt)
        self.assertNotIn("golden", prompt)
        self.assertNotIn("expected", prompt)

    def test_invalid_budget_and_generation_mode_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            generate_counterplan_descent(self.task, WidthBackend(), 0, 19)
        with self.assertRaises(ValueError):
            generate_counterplan_descent(self.task, WidthBackend(), 1, 19, generation_mode="other")


if __name__ == "__main__":
    unittest.main()
