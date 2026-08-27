# ExpressGen

ExpressGen is a deterministic Python CLI that turns a declarative JSON schema
into a working Express.js REST API backed by Prisma ORM. It builds a validated
intermediate model and renders every generated source file through Jinja2. It
does not call an LLM at runtime.

The repository includes the two roadmap features from the original brief:

- integer maps to Prisma Int and uses safe-integer request validation;
- number maps to Prisma Float and uses finite-number request validation;
- relation fields are resolved in a second pass;
- source-model foreign keys and target-model reverse arrays are injected;
- generated read and write routes receive independent RBAC middleware.

## Requirements

- Python 3.10 or newer
- Node.js 20.19 or newer for evaluating or running generated applications

The generated application targets Prisma ORM 7.10 and uses the required
database driver adapter. PostgreSQL and SQLite are supported.

## Repository layout

    .
    ├── expressgen/
    │   ├── __init__.py
    │   ├── __main__.py
    │   ├── cli.py
    │   ├── generator.py
    │   ├── prisma.py
    │   ├── router.py
    │   └── templates/
    ├── examples/
    │   └── input_schema.json
    ├── tests/
    │   ├── conftest.py
    │   └── test_generator.py
    ├── pyproject.toml
    └── README.md

## Install the generator

Linux or macOS:

    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -e ".[dev]"

Windows PowerShell:

    py -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install -e ".[dev]"

Validate the supplied example:

    expressgen validate examples/input_schema.json

Generate an application:

    expressgen generate examples/input_schema.json --output generated-app

ExpressGen refuses to overwrite a non-empty directory. To intentionally
regenerate it, add the force option:

    expressgen generate examples/input_schema.json --output generated-app --force

The module entry point is equivalent:

    python -m expressgen generate examples/input_schema.json -o generated-app

## Input contract

The root object supports database, api, and models.

    {
      "database": {
        "provider": "postgresql",
        "urlEnv": "DATABASE_URL"
      },
      "api": {
        "basePath": "/api"
      },
      "models": []
    }

Each model must have exactly one string or integer primary field.

### Scalar fields

| JSON type | Prisma type | JavaScript validation |
| --- | --- | --- |
| string | String | typeof value is string |
| boolean | Boolean | typeof value is boolean |
| integer | Int | Number.isSafeInteger |
| number | Float | finite JavaScript number |
| datetime | DateTime | parseable date string, converted to Date |
| json | Json | defined JSON value |

Supported scalar options are primary, unique, optional, default, auto,
updatedAt, and readOnly.

An automatic string primary key receives uuid(). An automatic integer primary
key receives autoincrement(). Safe default functions use an explicit object:

    "createdAt": {
      "type": "datetime",
      "default": {
        "function": "now"
      }
    }

### RBAC

Permissions are declared per model:

    "permissions": {
      "read": ["admin", "manager"],
      "write": ["admin"]
    }

Read permissions protect both GET routes. Write permissions protect POST, PUT,
and DELETE. Missing or empty role arrays make that action public.

Protected routes expect an HS256 JWT in the Authorization Bearer header. The
token can contain one role property or a roles array. Set JWT_SECRET in the
generated application's environment. The generated middleware validates
tokens; issuing login tokens remains the responsibility of the host
application or identity provider.

### Relations

A relation is declared on the source model:

    "author": {
      "type": "relation",
      "target": "User",
      "foreignKey": "authorId",
      "references": "id",
      "backref": "posts",
      "optional": false,
      "onDelete": "Cascade"
    }

Relation options:

| Option | Meaning | Default |
| --- | --- | --- |
| target or model | Target model name | required |
| foreignKey | Scalar key on the source | relation name plus referenced field |
| references | Primary or unique target scalar | target primary field |
| backref or reverse | Array field added to the target | plural source model |
| relationName | Prisma relation name | source, field, and target names |
| optional | Makes both relation and foreign key nullable | false |
| onDelete | Prisma referential action | Prisma default |
| onUpdate | Prisma referential action | Prisma default |

Resolution is deliberately two-pass. Pass one registers every model, scalar,
primary key, and unique field. Pass two resolves target models, confirms the
referenced field is primary or unique, injects or validates the source foreign
key, and adds a named reverse-relation array to the target. Multiple automatic
backrefs are disambiguated deterministically.

The generated router accepts the foreign-key scalar, such as authorId, in
create and update bodies. Nested relation writes are outside the current
contract.

## Generated application

The output contains:

    generated-app/
    ├── prisma/
    │   └── schema.prisma
    ├── routes/
    │   ├── User.js
    │   └── Post.js
    ├── middleware/
    │   └── auth.js
    ├── lib/
    │   └── prisma.js
    ├── index.js
    ├── package.json
    └── prisma.config.ts

For every model, ExpressGen creates:

| Method | Route | Prisma operation |
| --- | --- | --- |
| GET | /api/models | findMany |
| GET | /api/models/:id | findUnique |
| POST | /api/models | create |
| PUT | /api/models/:id | update |
| DELETE | /api/models/:id | delete |

Routes reject unknown and read-only body fields. Known Prisma not-found and
unique-conflict errors become HTTP 404 and 409 responses.

## Run a generated application

    cd generated-app
    cp .env.example .env
    npm install
    npm run prisma:generate
    npm run db:migrate -- --name init
    npm run dev

For PostgreSQL, update DATABASE_URL before running the migration. The health
endpoint is available at /health.

## Test strategy

Run:

    pytest

The tests cover mapping, RBAC rendering, relationship injection, optional
relations, deterministic output, CLI behavior, safe overwrite handling, and
invalid schema failures.

The Node.js test creates isolated stubs for Express, Prisma Client, JWT, and
database adapters. It then syntax-checks every generated JavaScript file and
imports the generated server without running npm install or connecting to a
database.

## Current boundaries

- Relations are singular source-to-target relationships with a reverse array.
- Each model has one scalar primary key; composite keys are not generated.
- Nested Prisma writes, pagination, filtering, login, and token issuance are
  intentionally outside the current schema contract.

These limits are validated rather than silently producing ambiguous code.
