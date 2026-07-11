"""Configuration schema migration and runtime compatibility projection."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping


CURRENT_CONFIG_SCHEMA_VERSION = 2
LEGACY_CONFIG_SCHEMA_VERSION = 1


class ConfigMigrationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MigrationNotice:
    path: str
    action: str
    message: str


@dataclass(frozen=True, slots=True)
class ConfigMigrationResult:
    config: dict[str, Any]
    notices: tuple[MigrationNotice, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.notices)


def config_schema_version(raw: Mapping[str, Any]) -> int:
    value = raw.get("schema_version", LEGACY_CONFIG_SCHEMA_VERSION)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigMigrationError("schema_version must be an integer")
    return value


def migrate_config(raw: Mapping[str, Any]) -> ConfigMigrationResult:
    """Return a non-mutating deterministic migration to schema v2."""

    if not isinstance(raw, Mapping):
        raise ConfigMigrationError("configuration root must be an object")
    version = config_schema_version(raw)
    if version == CURRENT_CONFIG_SCHEMA_VERSION:
        return ConfigMigrationResult(config=deepcopy(dict(raw)))
    if version != LEGACY_CONFIG_SCHEMA_VERSION:
        raise ConfigMigrationError(
            f"unsupported config schema_version={version}; "
            f"expected {LEGACY_CONFIG_SCHEMA_VERSION} or {CURRENT_CONFIG_SCHEMA_VERSION}"
        )

    source = deepcopy(dict(raw))
    old_pipeline = source.get("pipeline", {})
    if not isinstance(old_pipeline, Mapping):
        old_pipeline = {}

    pipeline: dict[str, Any] = {}
    notices: list[MigrationNotice] = []
    for field in (
        "debug_expand_gz",
        "extraction_workers",
        "diagnostic_scan_workers",
    ):
        if field in old_pipeline:
            pipeline[field] = deepcopy(old_pipeline[field])

    if "cleanup_extracted" in old_pipeline:
        pipeline["keep_workspace"] = not bool(old_pipeline["cleanup_extracted"])
        notices.append(
            MigrationNotice(
                path="pipeline.cleanup_extracted",
                action="renamed",
                message="moved to inverse pipeline.keep_workspace",
            )
        )
    else:
        pipeline["keep_workspace"] = False

    for field, message in (
        ("inner_extraction", "removed; the unified decompressor owns extraction"),
        ("generate_metadata", "removed; successful parses always emit metadata"),
        ("output_base_dir", "removed; output root is a runtime request"),
        ("result_json_mode", "removed; result.json is always compact"),
        ("cleanup_inner_archives", "removed with the old inner extraction stage"),
    ):
        if field in old_pipeline:
            notices.append(
                MigrationNotice(
                    path=f"pipeline.{field}",
                    action="removed",
                    message=message,
                )
            )

    recursive_extraction = bool(old_pipeline.get("recursive_extraction", False))
    if "recursive_extraction" in old_pipeline:
        notices.append(
            MigrationNotice(
                path="pipeline.recursive_extraction",
                action="moved",
                message="moved to each products.<name>.archive.recursive_extraction",
            )
        )

    migrated_products: dict[str, Any] = {}
    products = source.get("products", {})
    if isinstance(products, Mapping):
        for product_name, product_value in products.items():
            migrated_products[str(product_name)] = _migrate_product(
                str(product_name),
                product_value,
                recursive_extraction=recursive_extraction,
                notices=notices,
            )

    migrated = {
        "schema_version": CURRENT_CONFIG_SCHEMA_VERSION,
        "pipeline": pipeline,
        "products": migrated_products,
    }
    return ConfigMigrationResult(config=migrated, notices=tuple(notices))


def _migrate_product(
    product_name: str,
    raw: Any,
    *,
    recursive_extraction: bool,
    notices: list[MigrationNotice],
) -> Any:
    if not isinstance(raw, Mapping):
        return deepcopy(raw)

    discovery = deepcopy(dict(raw.get("discovery", {})))
    discovery_config = discovery.get("config", {})
    if not isinstance(discovery_config, dict):
        discovery_config = {}
        discovery["config"] = discovery_config
    extensions = discovery_config.pop("compressed_extensions", [])
    archive = {
        "recursive_extraction": recursive_extraction,
        "compressed_extensions": deepcopy(extensions),
    }
    if extensions:
        notices.append(
            MigrationNotice(
                path=(
                    f"products.{product_name}.discovery.config.compressed_extensions"
                ),
                action="moved",
                message=(
                    f"moved to products.{product_name}.archive.compressed_extensions"
                ),
            )
        )

    old_parser = raw.get("log_parser", raw.get("parser", {}))
    parser = deepcopy(dict(old_parser)) if isinstance(old_parser, Mapping) else old_parser
    parser_config: dict[str, Any] = {}
    mechanisms: dict[str, Any] = {}
    if isinstance(parser, dict):
        candidate = parser.get("config", {})
        if isinstance(candidate, Mapping):
            parser_config = deepcopy(dict(candidate))
        raw_modules = parser_config.pop("mechanism_modules", {})
        if isinstance(raw_modules, Mapping):
            mechanisms = {
                str(key): _migrate_mechanism(str(key), entry, product_name, notices)
                for key, entry in raw_modules.items()
            }
        if "active_period_gap_threshold" in parser_config:
            parser_config["active_period_gap_seconds"] = parser_config.pop(
                "active_period_gap_threshold"
            )
            notices.append(
                MigrationNotice(
                    path=(
                        f"products.{product_name}.log_parser.config."
                        "active_period_gap_threshold"
                    ),
                    action="renamed",
                    message="renamed to parser.config.active_period_gap_seconds",
                )
            )
        parser["config"] = parser_config

    notices.append(
        MigrationNotice(
            path=f"products.{product_name}.log_parser",
            action="renamed",
            message=f"renamed to products.{product_name}.parser",
        )
    )
    if mechanisms:
        notices.append(
            MigrationNotice(
                path=(
                    f"products.{product_name}.log_parser.config.mechanism_modules"
                ),
                action="moved",
                message=f"moved to products.{product_name}.mechanisms",
            )
        )

    return {
        "archive": archive,
        "discovery": discovery,
        "parser": parser,
        "mechanisms": mechanisms,
    }


def _migrate_mechanism(
    module_key: str,
    raw: Any,
    product_name: str,
    notices: list[MigrationNotice],
) -> Any:
    if not isinstance(raw, Mapping):
        return deepcopy(raw)
    entry = deepcopy(dict(raw))
    config = entry.get("config", {})
    config = deepcopy(dict(config)) if isinstance(config, Mapping) else config
    if isinstance(config, dict):
        legacy_dependency = config.pop("depends_on_module", None)
        if legacy_dependency:
            entry["depends_on"] = [str(legacy_dependency)]
            notices.append(
                MigrationNotice(
                    path=(
                        f"products.{product_name}.mechanisms.{module_key}."
                        "config.depends_on_module"
                    ),
                    action="moved",
                    message=(
                        f"moved to mechanisms.{module_key}.depends_on"
                    ),
                )
            )
        entry["config"] = config
    entry.setdefault("depends_on", [])
    return entry


def normalize_config_for_runtime(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Project schema v2 onto the legacy Pipeline/plugin configuration shape.

    The adapter is intentionally temporary.  It lets the architecture migrate
    independently while preserving existing plugin class paths and behavior.
    """

    version = config_schema_version(raw)
    if version == LEGACY_CONFIG_SCHEMA_VERSION:
        return deepcopy(dict(raw))
    if version != CURRENT_CONFIG_SCHEMA_VERSION:
        raise ConfigMigrationError(f"unsupported config schema_version={version}")

    normalized = deepcopy(dict(raw))
    pipeline = deepcopy(dict(raw.get("pipeline", {})))
    keep_workspace = bool(pipeline.pop("keep_workspace", False))
    pipeline["cleanup_extracted"] = not keep_workspace

    products: dict[str, Any] = {}
    all_extensions: list[str] = []
    raw_products = raw.get("products", {})
    if isinstance(raw_products, Mapping):
        for product_name, product_raw in raw_products.items():
            if not isinstance(product_raw, Mapping):
                products[str(product_name)] = deepcopy(product_raw)
                continue
            archive = product_raw.get("archive", {})
            archive = archive if isinstance(archive, Mapping) else {}
            extensions = archive.get("compressed_extensions", [])
            if isinstance(extensions, list):
                for extension in extensions:
                    value = str(extension)
                    if value not in all_extensions:
                        all_extensions.append(value)

            discovery = deepcopy(dict(product_raw.get("discovery", {})))
            discovery_config = discovery.get("config", {})
            if isinstance(discovery_config, dict):
                discovery_config.setdefault(
                    "compressed_extensions", deepcopy(extensions)
                )

            parser = deepcopy(dict(product_raw.get("parser", {})))
            parser_config = parser.get("config", {})
            if not isinstance(parser_config, dict):
                parser_config = {}
            if "active_period_gap_seconds" in parser_config:
                parser_config["active_period_gap_threshold"] = parser_config.pop(
                    "active_period_gap_seconds"
                )
            parser_config["mechanism_modules"] = _runtime_mechanisms(
                product_raw.get("mechanisms", {})
            )
            parser["config"] = parser_config
            products[str(product_name)] = {
                "discovery": discovery,
                "log_parser": parser,
            }

    default_product = next(iter(raw_products.values()), {}) if isinstance(raw_products, Mapping) else {}
    if isinstance(default_product, Mapping):
        archive = default_product.get("archive", {})
        if isinstance(archive, Mapping):
            pipeline["recursive_extraction"] = bool(
                archive.get("recursive_extraction", False)
            )

    normalized["pipeline"] = pipeline
    normalized["products"] = products
    normalized["compressed_extensions"] = all_extensions
    return normalized


def _runtime_mechanisms(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return deepcopy(raw)
    result: dict[str, Any] = {}
    for module_key, value in raw.items():
        if not isinstance(value, Mapping):
            result[str(module_key)] = deepcopy(value)
            continue
        entry = deepcopy(dict(value))
        dependencies = entry.get("depends_on", [])
        config = entry.get("config", {})
        if isinstance(config, dict) and isinstance(dependencies, list) and len(dependencies) == 1:
            config.setdefault("depends_on_module", dependencies[0])
        result[str(module_key)] = entry
    return result


def v2_product_runtime_config(
    raw: Mapping[str, Any],
    product: str,
) -> dict[str, Any]:
    """Return the selected product's compatibility runtime configuration."""

    normalized = normalize_config_for_runtime(raw)
    products = normalized.get("products", {})
    if not isinstance(products, Mapping) or product not in products:
        raise KeyError(f"unknown product: {product}")
    return deepcopy(dict(products[product]))
