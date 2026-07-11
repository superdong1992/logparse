"""Application services for config inspection and migration commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from backend.application.plugin_graph import ordered_mechanism_specs
from backend.config_migration import migrate_config, normalize_config_for_runtime
from backend.config_validation import validate_config


class ConfigurationError(ValueError):
    def __init__(self, errors: list[str] | tuple[str, ...]):
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


def load_config_file(path: Path) -> dict[str, Any]:
    return _resolve_product_includes(load_raw_config_file(path), path)


def load_raw_config_file(path: Path) -> dict[str, Any]:
    """Load one YAML document without expanding product include references."""

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError([f"cannot load config {path}: {exc}"]) from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(["configuration root must be an object"])
    return raw


def _resolve_product_includes(
    raw: dict[str, Any],
    config_path: Path,
) -> dict[str, Any]:
    products = raw.get("products")
    if not isinstance(products, Mapping):
        return raw
    resolved_products: dict[str, Any] = {}
    base = config_path.resolve(strict=False).parent
    for name, value in products.items():
        if not isinstance(value, Mapping) or "$include" not in value:
            resolved_products[str(name)] = value
            continue
        if set(value) != {"$include"}:
            raise ConfigurationError(
                [f"products.{name} $include cannot be combined with inline fields"]
            )
        include_value = value.get("$include")
        if not isinstance(include_value, str) or not include_value.strip():
            raise ConfigurationError([f"products.{name}.$include must be a path"])
        relative = Path(include_value)
        if relative.is_absolute():
            raise ConfigurationError([f"products.{name}.$include must be relative"])
        include_path = (base / relative).resolve(strict=False)
        try:
            include_path.relative_to(base)
        except ValueError as exc:
            raise ConfigurationError(
                [f"products.{name}.$include escapes the config directory"]
            ) from exc
        try:
            included = yaml.safe_load(include_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigurationError(
                [f"cannot load product config {include_path}: {exc}"]
            ) from exc
        if not isinstance(included, dict):
            raise ConfigurationError(
                [f"product config {include_path} must be an object"]
            )
        resolved_products[str(name)] = included
    result = dict(raw)
    result["products"] = resolved_products
    return result


def render_migrated_config(raw: Mapping[str, Any]) -> str:
    """Render a deterministic v2 YAML document for ``migrate-config``."""

    migrated = migrate_config(raw).config
    errors = validate_config(migrated)
    if errors:
        raise ConfigurationError(errors)
    return yaml.safe_dump(
        migrated,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


def explain_config(
    raw: Mapping[str, Any],
    *,
    product: str | None = None,
) -> dict[str, Any]:
    """Return the validated effective v2 config and plugin execution plan."""

    source_errors = validate_config(dict(raw))
    if source_errors:
        raise ConfigurationError(source_errors)
    migration = migrate_config(raw)
    effective = migration.config
    effective_errors = validate_config(effective)
    if effective_errors:
        raise ConfigurationError(effective_errors)

    products = effective.get("products", {})
    if not isinstance(products, Mapping):
        raise ConfigurationError(["products must be an object"])
    selected_names = [product] if product else list(products)
    explained_products: dict[str, Any] = {}
    for name in selected_names:
        if name not in products:
            raise ConfigurationError([f"unknown product: {name}"])
        product_config = products[name]
        mechanisms = product_config.get("mechanisms", {})
        specs = ordered_mechanism_specs(mechanisms)
        explained_products[name] = {
            "archive": product_config.get("archive", {}),
            "discovery": product_config.get("discovery", {}),
            "parser": product_config.get("parser", {}),
            "mechanisms": mechanisms,
            "execution_order": [spec.key for spec in specs],
            "dependencies": {
                spec.key: list(spec.dependencies) for spec in specs
            },
        }

    return {
        "schema_version": effective["schema_version"],
        "pipeline": effective.get("pipeline", {}),
        "products": explained_products,
        "migration_notices": [
            {
                "path": notice.path,
                "action": notice.action,
                "message": notice.message,
            }
            for notice in migration.notices
        ],
    }


def runtime_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and project config for the current compatibility Pipeline."""

    errors = validate_config(dict(raw))
    if errors:
        raise ConfigurationError(errors)
    return normalize_config_for_runtime(raw)
