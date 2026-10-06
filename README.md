# BorderNec

[한국어 안내](README.ko.md)

BorderNec replaces a successful tool plan when a verified alternative produces the same task result and exposes a strict subset of its foreign observer–personal-data relationships.

```text
Result(candidate) == Result(current)
J(candidate) < J(current)              # strict set inclusion
```

This repository contains the algorithm extracted from the research implementation, bounded exact enumeration, controlled examples, and tests. It reproduces the decision rule and search behavior. The manuscript's full model experiments, comparison methods, external benchmark adapters, model weights, raw runs, and paper assets are outside this release.

## Run the examples

Python 3.11 or later is required. The algorithm and tests use the Python standard library. Run these commands from the repository root.

```bash
PYTHONPATH=src python3 -m bordernec demo --out outputs/demo.json
PYTHONPATH=src python3 -m bordernec exact --out outputs/exact.json
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The five-call demo replays a fixed sequence of proposals for a hotel-search task. The initial plan exposes `name` and `email` to a foreign provider. A replacement returns the same symbolic hotel-search result with no personal data exposure. The audit records the failed booking proposal, the accepted strict-subset replacement, and subsequent rejections. This scripted example demonstrates algorithm behavior; it does not measure language-model performance.

The exact command enumerates successful bounded plans for all 12 controlled tasks and retains every plan without a same-result strict-subset witness. Each pruned plan includes its witness and the removed observations. Equal observation sets and incomparable minimal sets are retained. The independent golden fixture checks observations, outcomes, set relations, and frontier membership.

An editable installation also provides the `bordernec` command.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
bordernec demo --out outputs/demo.json
```

## Search with a local generator

`LocalBackend.generate(prompt, seed)` proposes plans. `CommandBackend` runs a local executable without a shell. The executable reads the prompt from standard input and writes its JSON proposal to standard output. The prompt supplies opaque action aliases such as `A1` and `A2`.

```bash
PYTHONPATH=src python3 -m bordernec search \
  --task B02_hotel_search_oversharing \
  --command '["python3", "examples/local_generator.py"]' \
  --budget 5 --seed 19 \
  --verified-results examples/verified_results.json \
  --out outputs/search.json
```

The supplied generator is a deterministic interface example. To use a local language model, replace the command with your model wrapper or implement `LocalBackend`. A command argument containing `{seed}` receives the generated call seed. The wrapper is responsible for using that seed in inference.

In `plan` mode, one model call proposes a complete plan with `{"steps":["A1", "A2"]}`. In `stepwise` mode, each call selects `{"action_id":"A1"}` from applicable actions. Forced single-action steps use zero model calls. `--budget` bounds the actual number of generator calls, including failed proposals. Add `--generation-mode stepwise` to use the latter mode.

## Verify result equality

The controlled examples use one symbolic outcome contract per task, expressed by required and forbidden facts. With no result registry, all successful plans in that task are compared within this declared outcome class. Other final-state facts are outside that contract.

For an environment with concrete outputs or state changes, a trusted executor must establish result signatures from fresh-state execution or replay. Pass its per-task signatures through `--verified-results` or the Python `verified_results` parameter. Missing signatures fail closed, and different signatures prevent a replacement. The model's proposed text provides no result-verification evidence.

```json
{
  "results": [
    {"task_id": "my_task", "steps": ["broad_action"], "signature": "verified-output-hash"},
    {"task_id": "my_task", "steps": ["narrow_action"], "signature": "verified-output-hash"}
  ]
}
```

The supplied result registry records the controlled B02 symbolic result. External operations were not replayed for these examples. Registry provenance, signature construction, and fresh-state execution belong to the integrating environment. Every Python frontier or witness comparison must use analyses from the same `TaskDomain`.

## Use the Python API

```python
from bordernec import (
    Plan, analyze_plan, enumerate_successful_plans,
    generate_counterplan_descent, jurisdictional_frontier, load_benchmark,
)

task = load_benchmark("examples/pilot_12.json").tasks[1]
verified_results = {
    ("search_with_identity",): "B02:top_three_hotels:no_reservation",
    ("search_minimal",): "B02:top_three_hotels:no_reservation",
}

plans = enumerate_successful_plans(task).plans
frontier = jurisdictional_frontier(
    [analyze_plan(task, plan) for plan in plans],
    verified_results=verified_results,
)
# frontier[0].plan.steps == ("search_minimal",)

# backend is your LocalBackend implementation.
# run = generate_counterplan_descent(
#     task, backend, call_budget=5, seed=19,
#     verified_results=verified_results,
# )
```

`is_same_result_witness` implements the shared relation for exact pruning and runtime acceptance. `find_dominating_witness` returns a concrete alternative for a dominated plan. A search run records each proposal, call count, seed, prompt hash, rejection reason, incumbent, and removed observations. `prefix_incumbents` records the incumbent available after each budget prefix; unused budget after a forced rollout repeats the available incumbent.

## Observation semantics and scope

`J` is a set over data identity, data subject, recipient entity, recipient country, and processing country. An action contributes personal data when the recipient country or processing country differs from the controller's home country. Repeated identical relationships contribute one set element; occurrences remain available for audit. Retrieval, provision, delegated processing, and storage use the same observation rule. Transfer mode, purpose, and exporter identity remain audit metadata.

The JSON registry supplies grounded actions, preconditions, effects, data bindings, action-use limits, and a maximum plan depth. Fields such as `data_atoms` and `data_atom` preserve the research schema. Register a different task file with `--benchmark PATH`. Bound enumeration explicitly with `--max-nodes`; exceeding the limit reports an error without returning a partial frontier.

Every accepted replacement preserves the verified result and removes observations while adding none. Bounded search can finish before finding a globally minimal plan. Exact enumeration establishes the frontier of the registered bounded plan space. Both depend on correct tool registrations and result verification. The release measures registered foreign observations and provides no measurements from foreign service endpoints or legal authorization decisions.

## Files and source provenance

| Location | Contents |
| --- | --- |
| `src/bordernec/semantics.py` | Execution, observation sets, same-result witnesses, frontier pruning |
| `src/bordernec/planner.py` | Bounded exact enumeration |
| `src/bordernec/local_model.py` | Seeded prompts, plan and stepwise descent, local generator interface |
| `src/bordernec/schema.py` | Registered task and plan schema |
| `examples/` | 12 controlled tasks, golden expectations, scripted generator, result-registry format |
| `tests/` | Golden validation, witness invariants, budget accounting, CLI integration |
| `SOURCE_PROVENANCE.json` | Original source hashes and extraction changes |

The source hashes identify the research implementation used for extraction. The release preserves its observation identity, bounded action semantics, seeded presentation, and strict-subset acceptance. Result-class handling also applies to exact pruning, and incomplete stepwise proposals remain in the audit. The complete algorithm can be checked with the included examples and tests without downloading a model or an external dataset.

## License

The code is distributed under the [MIT License](LICENSE).
