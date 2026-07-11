from datetime import datetime, timezone
import ast
from pathlib import Path

from backend.extensions.products.current.scopes import product_cycle_ref
from backend.extensions.mechanisms.module2 import Module2Plugin
from backend.models import (
    MechBoardCycle,
    MechLogEntry,
    MechResult,
    MechSlotOutput,
)


def test_cycle_identity_is_value_stable_and_not_path_based() -> None:
    start = datetime(2026, 1, 3, 1, 2, 3, tzinfo=timezone.utc)
    end = datetime(2026, 1, 3, 1, 3, 4, tzinfo=timezone.utc)
    first = product_cycle_ref("1", start, end, ordinal=2)
    second = product_cycle_ref("1", start, end, ordinal=2)

    assert first == second
    assert first.scope.identity == "slot:1"
    assert "2026-01-03T01:02:03" in first.cycle_id


def test_cpu_zero_projects_to_board_scope() -> None:
    ref = product_cycle_ref("2", None, None, cpu_id="0")
    assert ref.scope.identity == "slot:2"


def test_module2_does_not_use_process_local_object_ids() -> None:
    source_path = (
        Path(__file__).parents[2]
        / "backend"
        / "extensions"
        / "mechanisms"
        / "module2.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    id_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "id"
    ]
    assert id_calls == []


def test_module2_emits_structured_assignment_decision_without_raw_text() -> None:
    start = datetime(2026, 1, 3, 1, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 3, 2, 0, tzinfo=timezone.utc)
    upstream = MechResult(
        module_key="module1",
        slots=[
            MechSlotOutput(
                slot_id="1",
                board_cycles=[
                    MechBoardCycle(
                        dir_name="legacy-path-name",
                        start_time=start,
                        end_time=end,
                    )
                ],
            )
        ],
    )
    entry = MechLogEntry(
        timestamp=start,
        source="diagnostic",
        source_file="source.log",
        slot="1",
        process_name="worker",
        pid="7",
        context="private payload",
        raw="private raw line",
    )
    plugin = Module2Plugin({"module_name": "MODULE2"}, module_key="module2")

    result = plugin._build_result([entry], upstream)

    decision = result.slots[0].assignment_decisions[0]
    assert decision["status"] == "assigned"
    assert decision["scope_ref"] == "slot:1"
    assert decision["cycle_ref"].startswith("slot:1/interval:")
    assert "raw" not in decision
    assert "context" not in decision
