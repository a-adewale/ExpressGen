"""Schema validation, type mapping, and two-pass Prisma model resolution."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping


IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
ROUTE_RE = re.compile(r"^/[A-Za-z0-9_/-]*$")

TYPE_MAP: dict[str, str] = {
    "string": "String",
    "boolean": "Boolean",
    "integer": "Int",
    "number": "Float",
    "datetime": "DateTime",
    "json": "Json",
}

ALLOWED_DATABASE_PROVIDERS = {"postgresql", "sqlite"}
ALLOWED_REFERENTIAL_ACTIONS = {
    "Cascade",
    "Restrict",
    "NoAction",
    "SetNull",
    "SetDefault",
}

_MISSING = object()


class SchemaError(ValueError):
    """Raised when a declarative schema cannot be resolved safely."""


@dataclass
class DatabaseSpec:
    provider: str
    url_env: str


@dataclass
class ScalarField:
    name: str
    generic_type: str
    prisma_type: str
    optional: bool = False
    primary: bool = False
    unique: bool = False
    default_expression: str | None = None
    updated_at: bool = False
    read_only: bool = False
    injected: bool = False

    @property
    def rendered_type(self) -> str:
        return f"{self.prisma_type}{'?' if self.optional else ''}"

    @property
    def attributes(self) -> tuple[str, ...]:
        values: list[str] = []
        if self.primary:
            values.append("@id")
        elif self.unique:
            values.append("@unique")
        if self.default_expression is not None:
            values.append(f"@default({self.default_expression})")
        if self.updated_at:
            values.append("@updatedAt")
        return tuple(values)

    @property
    def has_server_value(self) -> bool:
        return self.default_expression is not None or self.updated_at


@dataclass
class RelationField:
    name: str
    target: str
    foreign_key: str
    references: str
    backref: str
    relation_name: str
    optional: bool
    on_delete: str | None = None
    on_update: str | None = None

    @property
    def rendered_type(self) -> str:
        return f"{self.target}{'?' if self.optional else ''}"

    @property
    def attributes(self) -> tuple[str, ...]:
        arguments = [
            json.dumps(self.relation_name),
            f"fields: [{self.foreign_key}]",
            f"references: [{self.references}]",
        ]
        if self.on_delete:
            arguments.append(f"onDelete: {self.on_delete}")
        if self.on_update:
            arguments.append(f"onUpdate: {self.on_update}")
        return ("@relation(" + ", ".join(arguments) + ")",)


@dataclass
class ReverseRelationField:
    name: str
    source: str
    relation_name: str

    @property
    def rendered_type(self) -> str:
        return f"{self.source}[]"

    @property
    def attributes(self) -> tuple[str, ...]:
        return (f"@relation({json.dumps(self.relation_name)})",)


PrismaField = ScalarField | RelationField | ReverseRelationField


@dataclass
class PendingRelation:
    source: str
    name: str
    config: Mapping[str, Any]


@dataclass
class ModelSpec:
    name: str
    route: str | None
    permissions: Mapping[str, Any]
    scalar_fields: list[ScalarField] = field(default_factory=list)
    relation_fields: list[RelationField] = field(default_factory=list)
    reverse_fields: list[ReverseRelationField] = field(default_factory=list)

    @property
    def primary_field(self) -> ScalarField:
        return next(field for field in self.scalar_fields if field.primary)

    @property
    def render_fields(self) -> list[PrismaField]:
        return [*self.scalar_fields, *self.relation_fields, *self.reverse_fields]

    def scalar_by_name(self, name: str) -> ScalarField | None:
        return next((field for field in self.scalar_fields if field.name == name), None)

    def all_field_names(self) -> set[str]:
        return {
            *(field.name for field in self.scalar_fields),
            *(field.name for field in self.relation_fields),
            *(field.name for field in self.reverse_fields),
        }


@dataclass
class ProjectSpec:
    database: DatabaseSpec
    api_base_path: str
    models: list[ModelSpec]

    def model_by_name(self, name: str) -> ModelSpec:
        return next(model for model in self.models if model.name == name)


def build_project_spec(payload: Mapping[str, Any]) -> ProjectSpec:
    """Validate the input and resolve scalar and relation fields in two passes."""

    if not isinstance(payload, Mapping):
        raise SchemaError("The schema root must be a JSON object.")

    _reject_unknown_keys(payload, {"models", "database", "api"}, "schema root")
    database = _parse_database(payload.get("database", {}))
    api_base_path = _parse_api(payload.get("api", {}))

    raw_models = payload.get("models")
    if not isinstance(raw_models, list) or not raw_models:
        raise SchemaError("'models' must be a non-empty array.")

    models: list[ModelSpec] = []
    pending_relations: list[PendingRelation] = []
    seen_models: set[str] = set()

    # Pass one: register every model and all declared scalar fields.
    for index, raw_model in enumerate(raw_models):
        context = f"models[{index}]"
        if not isinstance(raw_model, Mapping):
            raise SchemaError(f"{context} must be an object.")
        _reject_unknown_keys(
            raw_model,
            {"name", "route", "permissions", "fields"},
            context,
        )

        name = raw_model.get("name")
        _require_identifier(name, f"{context}.name")
        if name in seen_models:
            raise SchemaError(f"Duplicate model name '{name}'.")
        seen_models.add(name)

        route = raw_model.get("route")
        if route is not None:
            _require_route(route, f"{context}.route")

        permissions = raw_model.get("permissions", {})
        if not isinstance(permissions, Mapping):
            raise SchemaError(f"{context}.permissions must be an object.")

        raw_fields = raw_model.get("fields")
        if not isinstance(raw_fields, Mapping) or not raw_fields:
            raise SchemaError(f"{context}.fields must be a non-empty object.")

        model = ModelSpec(name=name, route=route, permissions=permissions)
        for field_name, raw_field in raw_fields.items():
            field_context = f"{context}.fields.{field_name}"
            _require_identifier(field_name, f"{context}.fields key")
            if not isinstance(raw_field, Mapping):
                raise SchemaError(f"{field_context} must be an object.")

            field_type = raw_field.get("type")
            if field_type == "relation":
                pending_relations.append(
                    PendingRelation(source=name, name=field_name, config=raw_field)
                )
                continue

            model.scalar_fields.append(
                _parse_scalar_field(field_name, raw_field, field_context)
            )

        primary_fields = [field for field in model.scalar_fields if field.primary]
        if len(primary_fields) != 1:
            raise SchemaError(
                f"Model '{name}' must declare exactly one scalar primary field; "
                f"found {len(primary_fields)}."
            )
        if primary_fields[0].generic_type not in {"string", "integer"}:
            raise SchemaError(
                f"Primary field '{name}.{primary_fields[0].name}' must use "
                "'string' or 'integer'."
            )
        models.append(model)

    project = ProjectSpec(
        database=database,
        api_base_path=api_base_path,
        models=models,
    )
    model_registry = {model.name: model for model in models}
    relation_names: set[str] = set()

    # Pass two: resolve targets, inject foreign keys, and add reverse arrays.
    for pending in pending_relations:
        source = model_registry[pending.source]
        relation = _resolve_relation(
            pending,
            source=source,
            registry=model_registry,
            used_relation_names=relation_names,
        )
        if relation.name in source.all_field_names():
            raise SchemaError(
                f"Relation field '{source.name}.{relation.name}' collides with "
                "another generated or declared field."
            )
        source.relation_fields.append(relation)
        target = model_registry[relation.target]
        target.reverse_fields.append(
            ReverseRelationField(
                name=relation.backref,
                source=source.name,
                relation_name=relation.relation_name,
            )
        )
        relation_names.add(relation.relation_name)

    return project


def _parse_database(raw_database: Any) -> DatabaseSpec:
    if not isinstance(raw_database, Mapping):
        raise SchemaError("'database' must be an object.")
    _reject_unknown_keys(raw_database, {"provider", "urlEnv"}, "database")
    provider = raw_database.get("provider", "postgresql")
    if provider not in ALLOWED_DATABASE_PROVIDERS:
        allowed = ", ".join(sorted(ALLOWED_DATABASE_PROVIDERS))
        raise SchemaError(f"database.provider must be one of: {allowed}.")
    url_env = raw_database.get("urlEnv", "DATABASE_URL")
    if not isinstance(url_env, str) or not ENV_NAME_RE.fullmatch(url_env):
        raise SchemaError("database.urlEnv must be an uppercase environment name.")
    return DatabaseSpec(provider=provider, url_env=url_env)


def _parse_api(raw_api: Any) -> str:
    if not isinstance(raw_api, Mapping):
        raise SchemaError("'api' must be an object.")
    _reject_unknown_keys(raw_api, {"basePath"}, "api")
    base_path = raw_api.get("basePath", "/api")
    _require_route(base_path, "api.basePath")
    if base_path != "/" and base_path.endswith("/"):
        raise SchemaError("api.basePath must not end with '/'.")
    return base_path


def _parse_scalar_field(
    name: str,
    raw_field: Mapping[str, Any],
    context: str,
) -> ScalarField:
    _reject_unknown_keys(
        raw_field,
        {
            "type",
            "primary",
            "unique",
            "optional",
            "default",
            "auto",
            "updatedAt",
            "readOnly",
        },
        context,
    )
    generic_type = raw_field.get("type")
    if generic_type not in TYPE_MAP:
        allowed = ", ".join([*TYPE_MAP, "relation"])
        raise SchemaError(f"{context}.type must be one of: {allowed}.")

    primary = _read_bool(raw_field, "primary", context, False)
    unique = _read_bool(raw_field, "unique", context, False)
    optional = _read_bool(raw_field, "optional", context, False)
    auto = _read_bool(raw_field, "auto", context, False)
    updated_at = _read_bool(raw_field, "updatedAt", context, False)
    read_only = _read_bool(raw_field, "readOnly", context, False)

    if primary and optional:
        raise SchemaError(f"{context} cannot be both primary and optional.")
    if updated_at and generic_type != "datetime":
        raise SchemaError(f"{context}.updatedAt is only valid for datetime fields.")
    if auto and "default" in raw_field:
        raise SchemaError(f"{context} cannot define both auto and default.")

    default_expression: str | None = None
    if auto:
        if generic_type == "integer" and primary:
            default_expression = "autoincrement()"
        elif generic_type == "string" and primary:
            default_expression = "uuid()"
        else:
            raise SchemaError(
                f"{context}.auto is supported only for string or integer primary fields."
            )
    elif "default" in raw_field:
        default_expression = _render_default(
            raw_field.get("default", _MISSING),
            generic_type,
            context,
        )

    return ScalarField(
        name=name,
        generic_type=generic_type,
        prisma_type=TYPE_MAP[generic_type],
        optional=optional,
        primary=primary,
        unique=unique,
        default_expression=default_expression,
        updated_at=updated_at,
        read_only=read_only or auto or updated_at,
    )


def _resolve_relation(
    pending: PendingRelation,
    *,
    source: ModelSpec,
    registry: Mapping[str, ModelSpec],
    used_relation_names: set[str],
) -> RelationField:
    context = f"{source.name}.{pending.name}"
    config = pending.config
    _reject_unknown_keys(
        config,
        {
            "type",
            "target",
            "model",
            "foreignKey",
            "references",
            "backref",
            "reverse",
            "relationName",
            "optional",
            "onDelete",
            "onUpdate",
        },
        context,
    )

    target_name = config.get("target", config.get("model"))
    if config.get("target") is not None and config.get("model") is not None:
        raise SchemaError(f"{context} must use either target or model, not both.")
    _require_identifier(target_name, f"{context}.target")
    if target_name not in registry:
        raise SchemaError(f"{context} references unknown model '{target_name}'.")
    target = registry[target_name]

    reference_name = config.get("references", target.primary_field.name)
    _require_identifier(reference_name, f"{context}.references")
    referenced_field = target.scalar_by_name(reference_name)
    if referenced_field is None:
        raise SchemaError(
            f"{context} references unknown scalar field "
            f"'{target_name}.{reference_name}'."
        )
    if not (referenced_field.primary or referenced_field.unique):
        raise SchemaError(
            f"{context} must reference a primary or unique field; "
            f"'{target_name}.{reference_name}' is neither."
        )

    optional = _read_bool(config, "optional", context, False)
    foreign_key = config.get(
        "foreignKey",
        f"{pending.name}{reference_name[:1].upper()}{reference_name[1:]}",
    )
    _require_identifier(foreign_key, f"{context}.foreignKey")
    if foreign_key == pending.name:
        raise SchemaError(f"{context}.foreignKey collides with the relation field.")

    existing_foreign_key = source.scalar_by_name(foreign_key)
    if existing_foreign_key is None:
        source.scalar_fields.append(
            ScalarField(
                name=foreign_key,
                generic_type=referenced_field.generic_type,
                prisma_type=referenced_field.prisma_type,
                optional=optional,
                injected=True,
            )
        )
    else:
        if existing_foreign_key.prisma_type != referenced_field.prisma_type:
            raise SchemaError(
                f"{context}.foreignKey '{foreign_key}' has type "
                f"{existing_foreign_key.generic_type}, expected "
                f"{referenced_field.generic_type}."
            )
        if existing_foreign_key.optional != optional:
            raise SchemaError(
                f"{context}.foreignKey '{foreign_key}' optionality must match "
                "the relation."
            )

    explicit_backref = config.get("backref", config.get("reverse"))
    if config.get("backref") is not None and config.get("reverse") is not None:
        raise SchemaError(f"{context} must use either backref or reverse, not both.")
    if explicit_backref is not None:
        _require_identifier(explicit_backref, f"{context}.backref")
        backref = explicit_backref
        if backref in target.all_field_names():
            raise SchemaError(
                f"{context}.backref '{backref}' already exists on model '{target.name}'."
            )
    else:
        backref = _available_backref(target, source.name, pending.name)

    relation_name = config.get(
        "relationName",
        f"{source.name}_{pending.name}_{target.name}",
    )
    _require_identifier(relation_name, f"{context}.relationName")
    if relation_name in used_relation_names:
        raise SchemaError(f"Duplicate relationName '{relation_name}'.")

    on_delete = _read_referential_action(config, "onDelete", context)
    on_update = _read_referential_action(config, "onUpdate", context)
    if optional is False and on_delete == "SetNull":
        raise SchemaError(f"{context}.onDelete SetNull requires an optional relation.")

    return RelationField(
        name=pending.name,
        target=target.name,
        foreign_key=foreign_key,
        references=reference_name,
        backref=backref,
        relation_name=relation_name,
        optional=optional,
        on_delete=on_delete,
        on_update=on_update,
    )


def _available_backref(target: ModelSpec, source_name: str, relation_name: str) -> str:
    base = _pluralize(_lower_first(source_name))
    if base not in target.all_field_names():
        return base
    suffix = relation_name[:1].upper() + relation_name[1:]
    candidate = f"{base}As{suffix}"
    counter = 2
    while candidate in target.all_field_names():
        candidate = f"{base}As{suffix}{counter}"
        counter += 1
    return candidate


def _pluralize(value: str) -> str:
    if value.endswith(("s", "x", "z", "ch", "sh")):
        return f"{value}es"
    if len(value) > 1 and value.endswith("y") and value[-2].lower() not in "aeiou":
        return f"{value[:-1]}ies"
    return f"{value}s"


def _lower_first(value: str) -> str:
    return value[:1].lower() + value[1:]


def _render_default(value: Any, generic_type: str, context: str) -> str:
    if value is _MISSING or value is None:
        raise SchemaError(f"{context}.default cannot be null.")

    if isinstance(value, Mapping):
        _reject_unknown_keys(value, {"function"}, f"{context}.default")
        function = value.get("function")
        if not isinstance(function, str):
            raise SchemaError(f"{context}.default.function must be a string.")
        return _validate_default_function(function, generic_type, context)

    if generic_type == "string":
        if not isinstance(value, str):
            raise SchemaError(f"{context}.default must be a string.")
        return json.dumps(value, ensure_ascii=False)
    if generic_type == "boolean":
        if not isinstance(value, bool):
            raise SchemaError(f"{context}.default must be a boolean.")
        return "true" if value else "false"
    if generic_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise SchemaError(f"{context}.default must be an integer.")
        return str(value)
    if generic_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SchemaError(f"{context}.default must be a number.")
        return json.dumps(value, allow_nan=False)
    if generic_type == "datetime":
        if not isinstance(value, str):
            raise SchemaError(
                f"{context}.default must use {{'function': 'now'}}."
            )
        raise SchemaError(f"{context}.default datetime literals are not supported.")
    if generic_type == "json":
        raise SchemaError(f"{context}.default is not supported for json fields.")
    raise SchemaError(f"Unsupported default at {context}.")


def _validate_default_function(function: str, generic_type: str, context: str) -> str:
    normalized = function.removesuffix("()")
    allowed_for_type = {
        "string": {"uuid", "cuid"},
        "integer": {"autoincrement"},
        "datetime": {"now"},
    }
    if normalized not in allowed_for_type.get(generic_type, set()):
        raise SchemaError(
            f"{context}.default function '{function}' is invalid for {generic_type}."
        )
    return f"{normalized}()"


def _read_bool(
    mapping: Mapping[str, Any],
    key: str,
    context: str,
    default: bool,
) -> bool:
    value = mapping.get(key, default)
    if not isinstance(value, bool):
        raise SchemaError(f"{context}.{key} must be a boolean.")
    return value


def _read_referential_action(
    mapping: Mapping[str, Any],
    key: str,
    context: str,
) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if value not in ALLOWED_REFERENTIAL_ACTIONS:
        allowed = ", ".join(sorted(ALLOWED_REFERENTIAL_ACTIONS))
        raise SchemaError(f"{context}.{key} must be one of: {allowed}.")
    return value


def _reject_unknown_keys(
    mapping: Mapping[str, Any],
    allowed: set[str],
    context: str,
) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        joined = ", ".join(unknown)
        raise SchemaError(f"Unknown key(s) in {context}: {joined}.")


def _require_identifier(value: Any, context: str) -> None:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise SchemaError(
            f"{context} must be an identifier beginning with a letter and "
            "containing only letters, numbers, or underscores."
        )


def _require_route(value: Any, context: str) -> None:
    if not isinstance(value, str) or not ROUTE_RE.fullmatch(value):
        raise SchemaError(
            f"{context} must start with '/' and contain only route-safe characters."
        )
