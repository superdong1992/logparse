"""插件动态加载器。"""

from __future__ import annotations

import importlib
from typing import Any, Mapping

from backend.application.plugin_graph import (
    ordered_mechanism_specs,
)


def load_plugin_class(class_path: str, base_class: type) -> type:
    """Load and type-check a plugin class without instantiating it."""

    try:
        module_path, class_name = class_path.rsplit(".", 1)
    except ValueError as exc:
        raise ValueError(
            f"invalid plugin class path {class_path!r}; expected module.Class"
        ) from exc
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    if not isinstance(cls, type) or not issubclass(cls, base_class):
        raise TypeError(f"{class_path} 不是 {base_class.__name__} 的子类")
    return cls


def instantiate_plugin(
    class_path: str,
    base_class: type,
    config: dict,
    **extra,
):
    """通过全限定类路径加载并实例化插件。

    示例:
        instantiate_plugin(
            "backend.plugins.default.scanner.ScannerPlugin",
            DirectoryDiscoveryPlugin,
            {"diagnostic_dir": "diag"},
            decompressor=some_decompressor,
        )
    """
    cls = load_plugin_class(class_path, base_class)
    return cls(config=config, **extra)


def instantiate_mechanism_plugins(
    entries: Mapping[str, Any],
    *,
    ts_extractor: Any = None,
) -> tuple[Any, ...]:
    """Validate, order, and instantiate enabled mechanism plugins.

    Dependency graph failures happen before the first plugin is instantiated
    and therefore before any log scan.
    """

    from backend.extensions.mechanisms.base import MechanismPlugin

    plugins: list[MechanismPlugin] = []
    for spec in ordered_mechanism_specs(entries):
        module_key = spec.key
        plugin = instantiate_plugin(
            spec.plugin,
            MechanismPlugin,
            dict(spec.config),
            module_key=module_key,
            ts_extractor=ts_extractor,
            dependencies=spec.dependencies,
        )
        plugins.append(plugin)
    return tuple(plugins)
