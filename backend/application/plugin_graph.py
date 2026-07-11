"""Deterministic mechanism plugin dependency planning.

Configuration order remains the tie breaker for otherwise independent
mechanisms, but dependencies are explicit and validated before any log scan.
"""

from __future__ import annotations

from dataclasses import dataclass
import heapq
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class MechanismSpec:
    key: str
    plugin: str
    config: Mapping[str, Any]
    dependencies: tuple[str, ...] = ()
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class PluginGraphIssue:
    code: str
    module: str
    message: str
    dependency: str | None = None


class PluginGraphError(ValueError):
    """Raised when mechanism dependencies cannot form an execution plan."""

    def __init__(self, issues: list[PluginGraphIssue] | tuple[PluginGraphIssue, ...]):
        self.issues = tuple(issues)
        super().__init__("; ".join(issue.message for issue in self.issues))


def _dependencies_for_entry(module_key: str, entry: Mapping[str, Any]) -> tuple[str, ...]:
    raw = entry.get("depends_on")
    if raw is None:
        config = entry.get("config", {})
        if isinstance(config, Mapping):
            legacy = config.get("depends_on_module")
            raw = [] if legacy in (None, "") else [legacy]
        else:
            raw = []

    if not isinstance(raw, (list, tuple)):
        raise PluginGraphError(
            [
                PluginGraphIssue(
                    code="invalid_dependencies",
                    module=module_key,
                    message=f"mechanism {module_key!r} depends_on must be a list",
                )
            ]
        )

    dependencies: list[str] = []
    seen: set[str] = set()
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise PluginGraphError(
                [
                    PluginGraphIssue(
                        code="invalid_dependency",
                        module=module_key,
                        message=(
                            f"mechanism {module_key!r} dependencies must be non-empty strings"
                        ),
                    )
                ]
            )
        dependency = value.strip()
        if dependency not in seen:
            seen.add(dependency)
            dependencies.append(dependency)
    return tuple(dependencies)


def build_mechanism_specs(
    entries: Mapping[str, Any],
) -> tuple[MechanismSpec, ...]:
    """Parse mechanism entries without importing plugin implementations."""

    if not isinstance(entries, Mapping):
        raise PluginGraphError(
            [
                PluginGraphIssue(
                    code="invalid_modules",
                    module="",
                    message="mechanisms must be an object",
                )
            ]
        )

    specs: list[MechanismSpec] = []
    issues: list[PluginGraphIssue] = []
    for raw_key, raw_entry in entries.items():
        key = str(raw_key)
        if not isinstance(raw_entry, Mapping):
            issues.append(
                PluginGraphIssue(
                    code="invalid_entry",
                    module=key,
                    message=f"mechanism {key!r} must be an object",
                )
            )
            continue
        enabled = raw_entry.get("enabled", True)
        if not isinstance(enabled, bool):
            issues.append(
                PluginGraphIssue(
                    code="invalid_enabled",
                    module=key,
                    message=f"mechanism {key!r} enabled must be a boolean",
                )
            )
            continue
        plugin = raw_entry.get("plugin", "")
        config = raw_entry.get("config", {})
        try:
            dependencies = _dependencies_for_entry(key, raw_entry)
        except PluginGraphError as exc:
            issues.extend(exc.issues)
            continue
        specs.append(
            MechanismSpec(
                key=key,
                plugin=str(plugin) if isinstance(plugin, str) else "",
                config=config if isinstance(config, Mapping) else {},
                dependencies=dependencies,
                enabled=enabled,
            )
        )

    if issues:
        raise PluginGraphError(issues)
    return tuple(specs)


def resolve_mechanism_order(entries: Mapping[str, Any]) -> tuple[str, ...]:
    """Return a stable topological order for enabled mechanisms.

    Missing, disabled, self, and cyclic dependencies fail together before
    execution.  Configuration insertion order is preserved between nodes that
    are simultaneously ready.
    """

    specs = build_mechanism_specs(entries)
    by_key = {spec.key: spec for spec in specs}
    enabled_specs = [spec for spec in specs if spec.enabled]
    issues: list[PluginGraphIssue] = []

    for spec in enabled_specs:
        for dependency in spec.dependencies:
            target = by_key.get(dependency)
            if target is None:
                issues.append(
                    PluginGraphIssue(
                        code="missing_dependency",
                        module=spec.key,
                        dependency=dependency,
                        message=(
                            f"mechanism {spec.key!r} depends on missing mechanism "
                            f"{dependency!r}"
                        ),
                    )
                )
            elif not target.enabled:
                issues.append(
                    PluginGraphIssue(
                        code="disabled_dependency",
                        module=spec.key,
                        dependency=dependency,
                        message=(
                            f"mechanism {spec.key!r} depends on disabled mechanism "
                            f"{dependency!r}"
                        ),
                    )
                )
            elif dependency == spec.key:
                issues.append(
                    PluginGraphIssue(
                        code="self_dependency",
                        module=spec.key,
                        dependency=dependency,
                        message=f"mechanism {spec.key!r} cannot depend on itself",
                    )
                )

    if issues:
        raise PluginGraphError(issues)

    order_index = {spec.key: index for index, spec in enumerate(enabled_specs)}
    indegree = {spec.key: 0 for spec in enabled_specs}
    dependants: dict[str, list[str]] = {spec.key: [] for spec in enabled_specs}
    for spec in enabled_specs:
        for dependency in spec.dependencies:
            indegree[spec.key] += 1
            dependants[dependency].append(spec.key)

    ready = [
        (order_index[key], key)
        for key, degree in indegree.items()
        if degree == 0
    ]
    heapq.heapify(ready)
    resolved: list[str] = []
    while ready:
        _position, key = heapq.heappop(ready)
        resolved.append(key)
        for dependant in dependants[key]:
            indegree[dependant] -= 1
            if indegree[dependant] == 0:
                heapq.heappush(ready, (order_index[dependant], dependant))

    if len(resolved) != len(enabled_specs):
        cycle_nodes = tuple(
            spec.key for spec in enabled_specs if indegree[spec.key] > 0
        )
        raise PluginGraphError(
            [
                PluginGraphIssue(
                    code="dependency_cycle",
                    module=key,
                    message=(
                        "mechanism dependency cycle detected: "
                        + " -> ".join((*cycle_nodes, cycle_nodes[0]))
                    ),
                )
                for key in cycle_nodes
            ]
        )

    return tuple(resolved)


def ordered_mechanism_specs(
    entries: Mapping[str, Any],
) -> tuple[MechanismSpec, ...]:
    """Return validated enabled specs in stable dependency order."""

    specs = {spec.key: spec for spec in build_mechanism_specs(entries)}
    return tuple(specs[key] for key in resolve_mechanism_order(entries))
