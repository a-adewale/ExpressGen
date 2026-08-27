"""Build deterministic Express router contexts from resolved Prisma models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .prisma import ModelSpec, ProjectSpec, ScalarField, SchemaError


@dataclass(frozen=True)
class ValidationRule:
    name: str
    generic_type: str
    required: bool
    nullable: bool


@dataclass(frozen=True)
class RouterSpec:
    model_name: str
    client_name: str
    file_name: str
    mount_path: str
    primary_name: str
    primary_type: str
    read_roles: tuple[str, ...]
    write_roles: tuple[str, ...]
    validation_rules: tuple[ValidationRule, ...]
    datetime_fields: tuple[str, ...]

    @property
    def uses_rbac(self) -> bool:
        return bool(self.read_roles or self.write_roles)


def build_router_specs(project: ProjectSpec) -> list[RouterSpec]:
    """Create router specifications in the same stable order as the models."""

    return [_build_router(project, model) for model in project.models]


def _build_router(project: ProjectSpec, model: ModelSpec) -> RouterSpec:
    read_roles, write_roles = _parse_permissions(model)
    writable_fields = [
        field for field in model.scalar_fields if not field.read_only
    ]
    rules = tuple(_validation_rule(field) for field in writable_fields)
    datetime_fields = tuple(
        field.name for field in writable_fields if field.generic_type == "datetime"
    )

    model_route = model.route or f"/{_pluralize(_lower_first(model.name))}"
    if project.api_base_path == "/":
        mount_path = model_route
    else:
        mount_path = f"{project.api_base_path}{model_route}"

    return RouterSpec(
        model_name=model.name,
        client_name=_lower_first(model.name),
        file_name=f"{model.name}.js",
        mount_path=mount_path,
        primary_name=model.primary_field.name,
        primary_type=model.primary_field.generic_type,
        read_roles=read_roles,
        write_roles=write_roles,
        validation_rules=rules,
        datetime_fields=datetime_fields,
    )


def _parse_permissions(model: ModelSpec) -> tuple[tuple[str, ...], tuple[str, ...]]:
    permissions = model.permissions
    unknown = sorted(set(permissions) - {"read", "write"})
    if unknown:
        joined = ", ".join(unknown)
        raise SchemaError(
            f"Unknown permission key(s) on model '{model.name}': {joined}."
        )
    return (
        _parse_role_list(permissions.get("read", []), model.name, "read"),
        _parse_role_list(permissions.get("write", []), model.name, "write"),
    )


def _parse_role_list(value: Any, model_name: str, action: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise SchemaError(
            f"Model '{model_name}' permission '{action}' must be an array."
        )
    roles: list[str] = []
    for role in value:
        if not isinstance(role, str) or not role.strip():
            raise SchemaError(
                f"Model '{model_name}' permission '{action}' contains "
                "an invalid role."
            )
        normalized = role.strip()
        if normalized not in roles:
            roles.append(normalized)
    return tuple(roles)


def _validation_rule(field: ScalarField) -> ValidationRule:
    return ValidationRule(
        name=field.name,
        generic_type=field.generic_type,
        required=not field.optional and not field.has_server_value,
        nullable=field.optional,
    )


def _pluralize(value: str) -> str:
    if value.endswith(("s", "x", "z", "ch", "sh")):
        return f"{value}es"
    if len(value) > 1 and value.endswith("y") and value[-2].lower() not in "aeiou":
        return f"{value[:-1]}ies"
    return f"{value}s"


def _lower_first(value: str) -> str:
    return value[:1].lower() + value[1:]
