from __future__ import annotations

import pytest

from backend.application.plugin_graph import (
    PluginGraphError,
    build_mechanism_specs,
    ordered_mechanism_specs,
    resolve_mechanism_order,
)


def _entry(*, depends_on=None, enabled=True):
    value = {"plugin": "example.Plugin", "enabled": enabled, "config": {}}
    if depends_on is not None:
        value["depends_on"] = depends_on
    return value


def test_resolve_order_moves_dependency_before_module_stably():
    modules = {
        "module2": _entry(depends_on=["module1"]),
        "independent": _entry(),
        "module1": _entry(),
    }

    assert resolve_mechanism_order(modules) == (
        "independent",
        "module1",
        "module2",
    )


def test_resolve_order_preserves_config_order_for_independent_modules():
    modules = {"z": _entry(), "a": _entry(), "m": _entry()}

    assert resolve_mechanism_order(modules) == ("z", "a", "m")


def test_legacy_depends_on_module_is_understood():
    modules = {
        "module2": {
            "plugin": "example.Plugin",
            "config": {"depends_on_module": "module1"},
        },
        "module1": _entry(),
    }

    specs = {spec.key: spec for spec in build_mechanism_specs(modules)}

    assert specs["module2"].dependencies == ("module1",)
    assert resolve_mechanism_order(modules) == ("module1", "module2")


@pytest.mark.parametrize(
    ("modules", "code"),
    [
        ({"b": _entry(depends_on=["missing"])}, "missing_dependency"),
        (
            {
                "a": _entry(enabled=False),
                "b": _entry(depends_on=["a"]),
            },
            "disabled_dependency",
        ),
        ({"a": _entry(depends_on=["a"])}, "self_dependency"),
        (
            {
                "a": _entry(depends_on=["b"]),
                "b": _entry(depends_on=["a"]),
            },
            "dependency_cycle",
        ),
    ],
)
def test_invalid_dependency_graph_fails_preflight(modules, code):
    with pytest.raises(PluginGraphError) as exc_info:
        resolve_mechanism_order(modules)

    assert code in {issue.code for issue in exc_info.value.issues}


def test_disabled_module_does_not_participate_in_order():
    modules = {
        "disabled": _entry(depends_on=["missing"], enabled=False),
        "enabled": _entry(),
    }

    assert resolve_mechanism_order(modules) == ("enabled",)


def test_dependency_list_is_deduplicated_without_reordering():
    specs = build_mechanism_specs(
        {
            "a": _entry(),
            "b": _entry(depends_on=["a", "a"]),
        }
    )

    assert specs[1].dependencies == ("a",)


def test_ordered_specs_returns_enabled_entries_in_plan_order():
    specs = ordered_mechanism_specs(
        {
            "b": _entry(depends_on=["a"]),
            "disabled": _entry(enabled=False),
            "a": _entry(),
        }
    )

    assert [spec.key for spec in specs] == ["a", "b"]
