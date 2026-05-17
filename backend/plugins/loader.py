""插件动态加载器。"""

from __future__ import annotations

import importlib


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
    module_path, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)

    if not issubclass(cls, base_class):
        raise TypeError(
            f"{class_path} 不是 {base_class.__name__} 的子类"
        )

    return cls(config=config, **extra)
