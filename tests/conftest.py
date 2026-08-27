"""Shared fixtures, including isolated Node.js dependency stubs."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest


@pytest.fixture
def node_executable() -> str:
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node.js is not installed.")
    return executable


@pytest.fixture
def mock_node_modules(tmp_path: Path) -> Path:
    """Create ESM stubs so generated modules can be evaluated without npm install."""

    root = tmp_path / "node_modules"

    def package(
        name: str,
        source: str,
        *,
        exports: str | dict[str, str] = "./index.js",
        file_name: str = "index.js",
    ) -> None:
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        metadata = {
            "name": name,
            "version": "0.0.0-test",
            "type": "module",
            "exports": exports,
        }
        (directory / "package.json").write_text(
            json.dumps(metadata),
            encoding="utf-8",
        )
        (directory / file_name).write_text(source, encoding="utf-8")

    package(
        "express",
        """
function createRouter() {
  const router = { routes: [] };
  for (const method of ["get", "post", "put", "delete"]) {
    router[method] = function register(path, ...handlers) {
      this.routes.push({ method, path, handlers });
      return this;
    };
  }
  return router;
}

function express() {
  return {
    disable() { return this; },
    use() { return this; },
    get() { return this; },
    listen(_port, callback) {
      if (callback) callback();
      return { close() {} };
    }
  };
}

express.Router = createRouter;
express.json = () => (_req, _res, next) => next();
export default express;
""".strip()
        + "\n",
    )
    package(
        "jsonwebtoken",
        """
const jwt = {
  verify() {
    return { role: "admin" };
  }
};
export default jwt;
""".strip()
        + "\n",
    )
    package(
        "@prisma/client",
        """
export class PrismaClient {
  constructor() {
    return new Proxy(this, {
      get(target, property) {
        if (property in target) return target[property];
        return {
          findMany: async () => [],
          findUnique: async () => null,
          create: async ({ data }) => data,
          update: async ({ data }) => data,
          delete: async () => ({})
        };
      }
    });
  }
}
""".strip()
        + "\n",
    )
    package(
        "@prisma/adapter-pg",
        "export class PrismaPg { constructor(options) { this.options = options; } }\n",
    )
    package(
        "@prisma/adapter-better-sqlite3",
        (
            "export class PrismaBetterSqlite3 { "
            "constructor(options) { this.options = options; } }\n"
        ),
    )
    package(
        "dotenv",
        "",
        exports={"./config": "./config.js"},
        file_name="config.js",
    )
    return root
