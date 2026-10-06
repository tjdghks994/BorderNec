from __future__ import annotations

from dataclasses import replace
import unittest
from pathlib import Path

from bordernec import (
    Plan, analyze_plan, enumerate_successful_plans, find_dominating_witness,
    is_same_result_witness, jurisdictional_frontier, load_benchmark,
)
from bordernec.planner import EnumerationLimitExceeded
from bordernec.semantics import TransferEvent, compare_footprints

ROOT = Path(__file__).resolve().parents[1]
TASKS = {task.id: task for task in load_benchmark(ROOT / "examples/pilot_12.json").tasks}


def observation(**overrides: str) -> TransferEvent:
    values = dict(data_atom="email", data_subject="user", exporter_entity="controller",
        importer_entity="provider", importer_entity_country="JP", processing_country="JP",
        transfer_mode="provision", purpose="search")
    return TransferEvent(**(values | overrides))


class AlgorithmTests(unittest.TestCase):
    def test_observation_identity_and_audit_metadata(self) -> None:
        base = frozenset({observation()})
        self.assertEqual(base, frozenset({observation(), observation()}))
        for values in ({"purpose": "storage"}, {"transfer_mode": "retrieval"},
                       {"exporter_entity": "other_controller"}):
            self.assertEqual(base, frozenset({observation(**values)}))
        for values in ({"data_atom": "name"}, {"data_subject": "other_user"},
                       {"importer_entity": "other_provider"}, {"importer_entity_country": "US"},
                       {"processing_country": "US"}):
            self.assertEqual(compare_footprints(base, frozenset({observation(**values)})), "incomparable")

    def test_empty_observation_sets_remain_equivalent(self) -> None:
        task = TASKS["B01_public_foreign_weather"]
        analyses = [analyze_plan(task, plan) for plan in enumerate_successful_plans(task).plans]
        self.assertTrue(all(not item.footprint for item in analyses))
        self.assertEqual(len(jurisdictional_frontier(analyses)), 2)

    def test_result_changed_and_unverified_plans_cannot_prune(self) -> None:
        task = TASKS["B02_hotel_search_oversharing"]
        broad = analyze_plan(task, Plan(("search_with_identity",)))
        narrow = analyze_plan(task, Plan(("search_minimal",)))
        signatures = {broad.plan.steps: "A", narrow.plan.steps: "B"}
        self.assertFalse(is_same_result_witness(broad, narrow, signatures))
        self.assertEqual(jurisdictional_frontier([broad, narrow], signatures), (broad, narrow))
        self.assertIsNone(find_dominating_witness(broad, [narrow], signatures))
        self.assertEqual(jurisdictional_frontier([broad, narrow], {broad.plan.steps: "A"}), (broad,))
        self.assertEqual(jurisdictional_frontier([broad, narrow], {}), ())

    def test_same_result_strict_subset_prunes_and_certifies_difference(self) -> None:
        task = TASKS["B02_hotel_search_oversharing"]
        broad = analyze_plan(task, Plan(("search_with_identity",)))
        narrow = analyze_plan(task, Plan(("search_minimal",)))
        signatures = {broad.plan.steps: "A", narrow.plan.steps: "A"}
        self.assertTrue(is_same_result_witness(broad, narrow, signatures))
        self.assertEqual(jurisdictional_frontier([broad, narrow], signatures), (narrow,))
        self.assertEqual(find_dominating_witness(broad, [narrow], signatures), narrow)
        self.assertEqual({item.data_atom for item in broad.footprint - narrow.footprint}, {"name", "email"})

    def test_smaller_incomparable_sets_are_both_kept(self) -> None:
        task = TASKS["B11_package_recipient_incomparability"]
        analyses = [analyze_plan(task, plan) for plan in enumerate_successful_plans(task).plans]
        self.assertEqual({len(item.footprint) for item in analyses}, {2, 4})
        self.assertEqual(len(jurisdictional_frontier(analyses)), 2)
        self.assertEqual(compare_footprints(analyses[0].footprint, analyses[1].footprint), "incomparable")

    def test_repetition_preserves_observations_and_occurrences(self) -> None:
        task = TASKS["B07_repeated_loyalty_transfer"]
        single = analyze_plan(task, Plan(("atomic_loyalty_update",)))
        repeated = analyze_plan(task, Plan(("prepare_loyalty_update", "commit_loyalty_update")))
        self.assertEqual(single.footprint, repeated.footprint)
        self.assertEqual(len(repeated.occurrences), 2)

    def test_retrieval_and_foreign_processing_are_observed(self) -> None:
        retrieval = analyze_plan(TASKS["B03_foreign_remote_retrieval"], Plan(("us_remote_match",)))
        processing = analyze_plan(TASKS["B09_kr_entity_us_processing"], Plan(("ocr_in_us_region",)))
        entity = analyze_plan(TASKS["B10_us_entity_kr_processing"], Plan(("sign_with_us_entity",)))
        self.assertEqual(next(iter(retrieval.footprint)).transfer_mode, "retrieval")
        self.assertEqual(processing.occurrences[0].foreign_triggers, ("processing_country",))
        self.assertEqual(entity.occurrences[0].foreign_triggers, ("recipient_country",))

    def test_failed_contract_and_partial_execution(self) -> None:
        task = TASKS["B02_hotel_search_oversharing"]
        failed = analyze_plan(task, Plan(("book_instead_of_search",)))
        broad = analyze_plan(task, Plan(("search_with_identity",)))
        self.assertFalse(is_same_result_witness(broad, failed))
        retrieval_task = TASKS["B03_foreign_remote_retrieval"]
        post_goal = analyze_plan(retrieval_task, Plan(("us_remote_match", "kr_read_email")))
        self.assertFalse(post_goal.execution.feasible)
        self.assertEqual(post_goal.execution.executed_step_count, 1)
        self.assertEqual(len(post_goal.footprint), 1)
        over_depth = analyze_plan(task, Plan(("search_with_identity",) * 2))
        self.assertFalse(over_depth.execution.feasible)
        self.assertFalse(over_depth.footprint)

    def test_unknown_action_and_missing_precondition(self) -> None:
        task = TASKS["B03_foreign_remote_retrieval"]
        for plan in (Plan(("unknown",)), Plan(("kr_match_email",))):
            analysis = analyze_plan(task, plan)
            self.assertFalse(analysis.execution.feasible)
            self.assertFalse(analysis.footprint)

    def test_use_count_and_enumeration_limit(self) -> None:
        task = TASKS["B03_foreign_remote_retrieval"]
        analysis = analyze_plan(task, Plan(("kr_read_email", "kr_read_email")))
        self.assertFalse(analysis.execution.feasible)
        self.assertEqual(analysis.execution.executed_step_count, 1)
        with self.assertRaises(EnumerationLimitExceeded):
            enumerate_successful_plans(task, max_nodes=1)

    def test_empty_success_and_unsolvable_task(self) -> None:
        task = TASKS["B01_public_foreign_weather"]
        already_done = replace(task, initial_facts=frozenset(task.outcome.must))
        self.assertEqual(enumerate_successful_plans(already_done).plans[0].steps, ())
        self.assertEqual(enumerate_successful_plans(replace(task, actions={})).plans, ())


if __name__ == "__main__":
    unittest.main()
