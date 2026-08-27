"""Behavioral tests for schema resolution and generated source code."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from expressgen.cli import main
from expressgen.generator import ExpressGenerator
from expressgen.prisma import SchemaError


@pytest.fixture
def sample_schema() -> dict:
    return {
        "database": {"provider": "postgresql"},
        "api": {"basePath": "/api"},
        "models": [
            {
                "name": "User",
                "permissions": {
                    "read": ["admin", "manager"],
                    "write": ["admin"],
                },
                "fields": {
                    "id": {"type": "string", "primary": True, "auto": True},
                    "email": {"type": "string", "unique": True},
                    "isActive": {
                        "type": "boolean",
                        "optional": True,
                        "default": True,
                    },
                },
            },
            {
                "name": "Post",
                "permissions": {
                    "read": ["admin", "manager"],
                    "write": ["admin"],
                },
                "fields": {
                    "id": {"type": "integer", "primary": True, "auto": True},
                    "title": {"type": "string"},
                    "viewCount": {"type": "integer", "default": 0},
                    "rating": {"type": "number", "optional": True},
                    "author": {
                        "type": "relation",
                        "target": "User",
                        "foreignKey": "authorId",
                        "backref": "posts",
                        "onDelete": "Cascade",
                    },
                },
            },
        ],
    }


def test_generates_prisma_types_crud_and_rbac(
    tmp_path: Path,
    sample_schema: dict,
) -> None:
    output = tmp_path / "app"
    ExpressGenerator().generate(sample_schema, output)

    prisma = (output / "prisma/schema.prisma").read_text(encoding="utf-8")
    router = (output / "routes/Post.js").read_text(encoding="utf-8")
    index = (output / "index.js").read_text(encoding="utf-8")

    assert "id Int @id @default(autoincrement())" in prisma
    assert "viewCount Int @default(0)" in prisma
    assert "rating Float?" in prisma
    assert 'requireRole(["admin", "manager"])' in router
    assert 'requireRole(["admin"])' in router
    assert "prisma.post.findMany" in router
    assert 'orderBy: { id: "asc" }' in router
    assert "Number.isSafeInteger(value)" in router
    assert "Number.isFinite(value)" in router
    assert 'app.use("/api/posts", postRouter);' in index


def test_two_pass_relation_injects_foreign_key_and_reverse_array(
    tmp_path: Path,
    sample_schema: dict,
) -> None:
    output = tmp_path / "app"
    ExpressGenerator().generate(sample_schema, output)
    prisma = (output / "prisma/schema.prisma").read_text(encoding="utf-8")
    router = (output / "routes/Post.js").read_text(encoding="utf-8")

    assert "authorId String" in prisma
    assert (
        'author User @relation("Post_author_User", fields: [authorId], '
        "references: [id], onDelete: Cascade)"
    ) in prisma
    assert 'posts Post[] @relation("Post_author_User")' in prisma
    assert '"authorId": Object.freeze({' in router
    assert "required: true" in router


def test_optional_relation_produces_nullable_foreign_key_and_relation(
    tmp_path: Path,
) -> None:
    schema = {
        "database": {"provider": "sqlite"},
        "models": [
            {
                "name": "Team",
                "fields": {
                    "code": {"type": "string", "primary": True},
                    "name": {"type": "string"},
                },
            },
            {
                "name": "Member",
                "fields": {
                    "id": {"type": "integer", "primary": True, "auto": True},
                    "team": {
                        "type": "relation",
                        "target": "Team",
                        "references": "code",
                        "optional": True,
                        "onDelete": "SetNull",
                    },
                },
            },
        ],
    }
    output = tmp_path / "app"
    ExpressGenerator().generate(schema, output)
    prisma = (output / "prisma/schema.prisma").read_text(encoding="utf-8")
    package = json.loads((output / "package.json").read_text(encoding="utf-8"))

    assert "teamCode String?" in prisma
    assert (
        'team Team? @relation("Member_team_Team", fields: [teamCode], '
        "references: [code], onDelete: SetNull)"
    ) in prisma
    assert "@prisma/adapter-better-sqlite3" in package["dependencies"]
    assert "@prisma/adapter-pg" not in package["dependencies"]


def test_duplicate_automatic_backrefs_are_disambiguated(tmp_path: Path) -> None:
    schema = {
        "models": [
            {
                "name": "User",
                "fields": {
                    "id": {"type": "string", "primary": True},
                },
            },
            {
                "name": "Post",
                "fields": {
                    "id": {"type": "string", "primary": True},
                    "author": {"type": "relation", "target": "User"},
                    "reviewer": {"type": "relation", "target": "User"},
                },
            },
        ]
    }
    output = tmp_path / "app"
    ExpressGenerator().generate(schema, output)
    prisma = (output / "prisma/schema.prisma").read_text(encoding="utf-8")

    assert 'posts Post[] @relation("Post_author_User")' in prisma
    assert 'postsAsReviewer Post[] @relation("Post_reviewer_User")' in prisma


def test_permissions_are_optional_and_empty_means_public(tmp_path: Path) -> None:
    schema = {
        "models": [
            {
                "name": "Status",
                "fields": {
                    "id": {"type": "integer", "primary": True},
                    "value": {"type": "string"},
                },
            }
        ]
    }
    output = tmp_path / "app"
    ExpressGenerator().generate(schema, output)
    router = (output / "routes/Status.js").read_text(encoding="utf-8")

    assert "authenticate" not in router
    assert "requireRole" not in router
    assert 'app.use("/api/statuses"' in (
        output / "index.js"
    ).read_text(encoding="utf-8")


def test_generation_is_byte_for_byte_deterministic(
    tmp_path: Path,
    sample_schema: dict,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    generator = ExpressGenerator()
    first_result = generator.generate(sample_schema, first)
    second_result = generator.generate(sample_schema, second)

    first_files = {
        path.relative_to(first): path.read_bytes() for path in first_result.files
    }
    second_files = {
        path.relative_to(second): path.read_bytes() for path in second_result.files
    }
    assert first_files == second_files


def test_non_empty_output_requires_force(
    tmp_path: Path,
    sample_schema: dict,
) -> None:
    output = tmp_path / "app"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("existing", encoding="utf-8")

    with pytest.raises(SchemaError, match="not empty"):
        ExpressGenerator().generate(sample_schema, output)
    assert marker.read_text(encoding="utf-8") == "existing"

    ExpressGenerator().generate(sample_schema, output, force=True)
    assert not marker.exists()
    assert (output / "index.js").exists()


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda schema: schema["models"][1]["fields"]["author"].update(
                {"target": "Missing"}
            ),
            "unknown model",
        ),
        (
            lambda schema: schema["models"][1]["fields"]["rating"].update(
                {"type": "decimal"}
            ),
            "must be one of",
        ),
        (
            lambda schema: schema["models"][0]["permissions"].update(
                {"write": "admin"}
            ),
            "must be an array",
        ),
    ],
)
def test_invalid_schema_fails_before_writing(
    tmp_path: Path,
    sample_schema: dict,
    mutator,
    message: str,
) -> None:
    mutator(sample_schema)
    output = tmp_path / "app"
    with pytest.raises(SchemaError, match=message):
        ExpressGenerator().generate(sample_schema, output)
    assert not output.exists()


def test_cli_validate_and_generate(
    tmp_path: Path,
    sample_schema: dict,
    capsys: pytest.CaptureFixture[str],
) -> None:
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(sample_schema), encoding="utf-8")
    output = tmp_path / "generated"

    assert main(["validate", str(schema_path)]) == 0
    assert "Schema is valid: 2 model(s)." in capsys.readouterr().out
    assert main(["generate", str(schema_path), "-o", str(output)]) == 0
    assert (output / "routes/User.js").exists()


def test_generated_javascript_syntax_and_module_evaluation(
    tmp_path: Path,
    sample_schema: dict,
    node_executable: str,
    mock_node_modules: Path,
) -> None:
    output = tmp_path / "app"
    ExpressGenerator().generate(sample_schema, output)
    javascript_files = sorted(output.rglob("*.js"))

    for source_file in javascript_files:
        checked = subprocess.run(
            [node_executable, "--check", str(source_file)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert checked.returncode == 0, checked.stderr

    environment = os.environ.copy()
    environment["DATABASE_URL"] = "postgresql://test:test@localhost:5432/test"
    imported = subprocess.run(
        [
            node_executable,
            "-e",
            (
                "import("
                + json.dumps((output / "index.js").as_uri())
                + ").then(() => process.exit(0))"
                + ".catch((error) => { console.error(error); process.exit(1); })"
            ),
        ],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert imported.returncode == 0, imported.stderr
