"""Core deterministic Jinja2 rendering pipeline."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .prisma import ProjectSpec, SchemaError, build_project_spec
from .router import RouterSpec, build_router_specs


@dataclass(frozen=True)
class GenerationResult:
    output_dir: Path
    files: tuple[Path, ...]


def load_schema(path: str | Path) -> Mapping[str, Any]:
    """Load a JSON schema with actionable syntax and file errors."""

    schema_path = Path(path)
    try:
        with schema_path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except json.JSONDecodeError as exc:
        raise SchemaError(
            f"Invalid JSON in {schema_path} at line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}."
        ) from exc
    if not isinstance(payload, Mapping):
        raise SchemaError("The schema root must be a JSON object.")
    return payload


class ExpressGenerator:
    """Build an intermediate model and render a complete Node.js application."""

    def __init__(self, template_dir: str | Path | None = None) -> None:
        directory = (
            Path(template_dir)
            if template_dir is not None
            else Path(__file__).with_name("templates")
        )
        self.environment = Environment(
            loader=FileSystemLoader(directory),
            undefined=StrictUndefined,
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

    def build(self, payload: Mapping[str, Any]) -> ProjectSpec:
        """Validate the schema and return its fully resolved intermediate form."""

        project = build_project_spec(payload)
        # Router construction validates permissions before any output is written.
        build_router_specs(project)
        return project

    def generate(
        self,
        payload: Mapping[str, Any],
        output_dir: str | Path,
        *,
        force: bool = False,
    ) -> GenerationResult:
        """Render the project atomically into output_dir."""

        project = self.build(payload)
        routers = build_router_specs(project)
        output = Path(output_dir).expanduser().resolve()
        self._validate_output_target(output, force)
        output.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(
            prefix=f".{output.name}-expressgen-",
            dir=output.parent,
        ) as temporary:
            stage = Path(temporary) / output.name
            stage.mkdir()
            relative_files = self._render_project(stage, project, routers)

            if output.exists():
                if output.is_dir():
                    shutil.rmtree(output)
                else:
                    output.unlink()
            stage.replace(output)

        return GenerationResult(
            output_dir=output,
            files=tuple(output / path for path in relative_files),
        )

    def _render_project(
        self,
        stage: Path,
        project: ProjectSpec,
        routers: list[RouterSpec],
    ) -> list[Path]:
        common = {"project": project, "routers": routers}
        rendered: list[Path] = []

        files: list[tuple[str, Path, dict[str, Any]]] = [
            ("schema.prisma.j2", Path("prisma/schema.prisma"), common),
            ("index.js.j2", Path("index.js"), common),
            ("auth.js.j2", Path("middleware/auth.js"), common),
            ("prisma.js.j2", Path("lib/prisma.js"), common),
            ("package.json.j2", Path("package.json"), common),
            ("prisma.config.ts.j2", Path("prisma.config.ts"), common),
            ("env.example.j2", Path(".env.example"), common),
            ("generated.gitignore.j2", Path(".gitignore"), common),
        ]
        for router in routers:
            files.append(
                (
                    "router.js.j2",
                    Path("routes") / router.file_name,
                    {**common, "router": router},
                )
            )

        for template_name, relative_path, context in files:
            destination = stage / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            template = self.environment.get_template(template_name)
            destination.write_text(template.render(**context), encoding="utf-8")
            rendered.append(relative_path)
        return rendered

    @staticmethod
    def _validate_output_target(output: Path, force: bool) -> None:
        if output == Path(output.anchor):
            raise SchemaError("Refusing to generate into the filesystem root.")
        if not output.exists():
            return
        if output.is_file():
            if not force:
                raise SchemaError(
                    f"Output path '{output}' is a file; use --force to replace it."
                )
            return
        if any(output.iterdir()) and not force:
            raise SchemaError(
                f"Output directory '{output}' is not empty; use --force to replace it."
            )
