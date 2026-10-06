from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


TRANSFER_MODES = frozenset(
    {"provision", "retrieval", "delegated_processing", "storage"}
)


class SchemaError(ValueError):
    """Raised when a benchmark file violates the controlled-domain schema."""


def _required(obj: dict[str, Any], key: str, context: str) -> Any:
    if key not in obj:
        raise SchemaError(f"{context}: missing required field {key!r}")
    return obj[key]


def _string_tuple(value: Any, context: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise SchemaError(f"{context}: expected a list of strings")
    return tuple(value)


def _country_code(value: Any, context: str) -> str:
    country = str(value)
    if len(country) != 2 or not country.isalpha() or country != country.upper():
        raise SchemaError(
            f"{context}: expected an uppercase ISO 3166-1 alpha-2 code"
        )
    return country


@dataclass(frozen=True)
class Controller:
    entity: str
    country: str


@dataclass(frozen=True)
class DataAtom:
    id: str
    personal: bool
    subject: str = "user"
    category: str = "unspecified"
    source_path: str | None = None


@dataclass(frozen=True)
class Tool:
    id: str
    description: str
    recipient_entity: str
    recipient_country: str
    processing_country: str
    default_transfer_mode: str
    purpose: str


@dataclass(frozen=True)
class DataAccess:
    atom: str
    mode: str | None = None
    purpose: str | None = None


@dataclass(frozen=True)
class Action:
    id: str
    description: str
    tool: str
    pre: tuple[str, ...]
    add: tuple[str, ...]
    delete: tuple[str, ...] = ()
    data: tuple[DataAccess, ...] = ()
    required_data: tuple[str, ...] = ()
    max_uses: int = 1


@dataclass(frozen=True)
class OutcomeContract:
    must: tuple[str, ...]
    must_not: tuple[str, ...] = ()


@dataclass(frozen=True)
class Policy:
    denied_recipients: tuple[str, ...] = ()
    denied_countries: tuple[str, ...] = ()
    denied_modes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    steps: tuple[str, ...]
    source: str = "unknown"
    raw_output: str | None = field(default=None, compare=False)

    @property
    def id(self) -> str:
        return " -> ".join(self.steps) if self.steps else "<empty>"


@dataclass(frozen=True)
class TaskDomain:
    id: str
    split: str
    semantic_case: str
    request: str
    controller: Controller
    initial_facts: frozenset[str]
    outcome: OutcomeContract
    max_depth: int
    data_atoms: dict[str, DataAtom]
    tools: dict[str, Tool]
    actions: dict[str, Action]
    policy: Policy
    expected: dict[str, Any]
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Benchmark:
    name: str
    version: str
    tasks: tuple[TaskDomain, ...]
    source: dict[str, Any] = field(default_factory=dict)


def _parse_controller(raw: dict[str, Any], context: str) -> Controller:
    return Controller(
        entity=str(_required(raw, "entity", context)),
        country=_country_code(_required(raw, "country", context), f"{context}.country"),
    )


def _parse_data_atom(raw: dict[str, Any], context: str) -> DataAtom:
    personal = _required(raw, "personal", context)
    if not isinstance(personal, bool):
        raise SchemaError(f"{context}.personal: expected a boolean")
    return DataAtom(
        id=str(_required(raw, "id", context)),
        personal=personal,
        subject=str(raw.get("subject", "user")),
        category=str(raw.get("category", "unspecified")),
        source_path=(
            str(raw["source_path"])
            if raw.get("source_path") is not None
            else None
        ),
    )


def _parse_tool(raw: dict[str, Any], context: str) -> Tool:
    mode = str(_required(raw, "default_transfer_mode", context))
    if mode not in TRANSFER_MODES:
        raise SchemaError(
            f"{context}: unsupported transfer mode {mode!r}; "
            f"expected one of {sorted(TRANSFER_MODES)}"
        )
    return Tool(
        id=str(_required(raw, "id", context)),
        description=str(_required(raw, "description", context)),
        recipient_entity=str(_required(raw, "recipient_entity", context)),
        recipient_country=_country_code(
            _required(raw, "recipient_country", context),
            f"{context}.recipient_country",
        ),
        processing_country=_country_code(
            _required(raw, "processing_country", context),
            f"{context}.processing_country",
        ),
        default_transfer_mode=mode,
        purpose=str(_required(raw, "purpose", context)),
    )


def _parse_access(raw: dict[str, Any], context: str) -> DataAccess:
    mode = raw.get("mode")
    if mode is not None and mode not in TRANSFER_MODES:
        raise SchemaError(f"{context}: unsupported transfer mode {mode!r}")
    return DataAccess(
        atom=str(_required(raw, "atom", context)),
        mode=str(mode) if mode is not None else None,
        purpose=str(raw["purpose"]) if "purpose" in raw else None,
    )


def _parse_action(raw: dict[str, Any], context: str) -> Action:
    data_raw = raw.get("data", [])
    if not isinstance(data_raw, list):
        raise SchemaError(f"{context}.data: expected a list")
    max_uses = int(raw.get("max_uses", 1))
    if max_uses < 1:
        raise SchemaError(f"{context}.max_uses: must be at least 1")
    data = tuple(
        _parse_access(item, f"{context}.data[{i}]")
        for i, item in enumerate(data_raw)
    )
    required_data = _string_tuple(
        raw.get("required_data", [item.atom for item in data]),
        f"{context}.required_data",
    )
    if not set(required_data).issubset({item.atom for item in data}):
        raise SchemaError(
            f"{context}.required_data: every required atom must be present "
            "in the grounded action binding"
        )
    return Action(
        id=str(_required(raw, "id", context)),
        description=str(_required(raw, "description", context)),
        tool=str(_required(raw, "tool", context)),
        pre=_string_tuple(raw.get("pre", []), f"{context}.pre"),
        add=_string_tuple(raw.get("add", []), f"{context}.add"),
        delete=_string_tuple(raw.get("delete", []), f"{context}.delete"),
        data=data,
        required_data=required_data,
        max_uses=max_uses,
    )


def _parse_policy(raw: dict[str, Any], context: str) -> Policy:
    denied_countries = _string_tuple(
        raw.get("denied_countries", []), f"{context}.denied_countries"
    )
    for index, country in enumerate(denied_countries):
        _country_code(country, f"{context}.denied_countries[{index}]")
    denied_modes = _string_tuple(
        raw.get("denied_modes", []), f"{context}.denied_modes"
    )
    unknown_modes = set(denied_modes) - TRANSFER_MODES
    if unknown_modes:
        raise SchemaError(
            f"{context}.denied_modes: unsupported modes "
            f"{sorted(unknown_modes)}"
        )
    return Policy(
        denied_recipients=_string_tuple(
            raw.get("denied_recipients", []), f"{context}.denied_recipients"
        ),
        denied_countries=denied_countries,
        denied_modes=denied_modes,
    )


def _parse_task(raw: dict[str, Any], index: int) -> TaskDomain:
    context = f"tasks[{index}]"
    task_id = str(_required(raw, "id", context))

    atoms_list = _required(raw, "data_atoms", context)
    tools_list = _required(raw, "tools", context)
    actions_list = _required(raw, "actions", context)
    if not isinstance(atoms_list, list):
        raise SchemaError(f"{context}.data_atoms: expected a list")
    if not isinstance(tools_list, list):
        raise SchemaError(f"{context}.tools: expected a list")
    if not isinstance(actions_list, list):
        raise SchemaError(f"{context}.actions: expected a list")

    atoms = {
        atom.id: atom
        for i, item in enumerate(atoms_list)
        for atom in [_parse_data_atom(item, f"{context}.data_atoms[{i}]")]
    }
    tools = {
        tool.id: tool
        for i, item in enumerate(tools_list)
        for tool in [_parse_tool(item, f"{context}.tools[{i}]")]
    }
    actions = {
        action.id: action
        for i, item in enumerate(actions_list)
        for action in [_parse_action(item, f"{context}.actions[{i}]")]
    }

    if len(atoms) != len(atoms_list):
        raise SchemaError(f"{context}: duplicate data atom id")
    if len(tools) != len(tools_list):
        raise SchemaError(f"{context}: duplicate tool id")
    if len(actions) != len(actions_list):
        raise SchemaError(f"{context}: duplicate action id")

    for action in actions.values():
        if action.tool not in tools:
            raise SchemaError(
                f"{context}.actions[{action.id}]: unknown tool {action.tool!r}"
            )
        for access in action.data:
            if access.atom not in atoms:
                raise SchemaError(
                    f"{context}.actions[{action.id}]: "
                    f"unknown data atom {access.atom!r}"
                )

    outcome_raw = _required(raw, "outcome", context)
    outcome = OutcomeContract(
        must=_string_tuple(
            _required(outcome_raw, "must", f"{context}.outcome"),
            f"{context}.outcome.must",
        ),
        must_not=_string_tuple(
            outcome_raw.get("must_not", []), f"{context}.outcome.must_not"
        ),
    )
    max_depth = int(_required(raw, "max_depth", context))
    if max_depth < 0:
        raise SchemaError(f"{context}.max_depth: must be non-negative")

    return TaskDomain(
        id=task_id,
        split=str(raw.get("split", "controlled")),
        semantic_case=str(raw.get("semantic_case", "unspecified")),
        request=str(_required(raw, "request", context)),
        controller=_parse_controller(
            _required(raw, "controller", context), f"{context}.controller"
        ),
        initial_facts=frozenset(
            _string_tuple(
                _required(raw, "initial_facts", context),
                f"{context}.initial_facts",
            )
        ),
        outcome=outcome,
        max_depth=max_depth,
        data_atoms=atoms,
        tools=tools,
        actions=actions,
        policy=_parse_policy(raw.get("policy", {}), f"{context}.policy"),
        expected=dict(raw.get("expected", {})),
        provenance=dict(raw.get("provenance", {})),
    )


def load_benchmark(path: str | Path) -> Benchmark:
    benchmark_path = Path(path)
    try:
        raw = json.loads(benchmark_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SchemaError(f"{benchmark_path}: invalid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise SchemaError(f"{benchmark_path}: root must be an object")
    tasks_raw = _required(raw, "tasks", str(benchmark_path))
    if not isinstance(tasks_raw, list):
        raise SchemaError(f"{benchmark_path}.tasks: expected a list")
    tasks = tuple(_parse_task(task, i) for i, task in enumerate(tasks_raw))
    task_ids = [task.id for task in tasks]
    if len(set(task_ids)) != len(task_ids):
        raise SchemaError(f"{benchmark_path}: duplicate task id")
    return Benchmark(
        name=str(raw.get("name", benchmark_path.stem)),
        version=str(raw.get("version", "0")),
        tasks=tasks,
        source=dict(raw.get("source", {})),
    )
