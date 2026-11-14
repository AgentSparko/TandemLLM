"""Validate an engine API response against the engine's contract (engine-api-v1) JSON Schemas.

    curl -s -H "Authorization: Bearer $QSE_ADMIN_TOKEN" localhost:8000/v1/dashboard/summary \\
        | python tools/contract_check.py summary
    python tools/contract_check.py usage response.json
    python tools/contract_check.py engine/disk response.json

A name is `<name>.schema.json` in the engine's `contracts/engine-api-v1/dashboard/` (the
dashboard answers), or a path under `contracts/engine-api-v1/` when it holds a slash (`engine/disk`,
`health`: `./health`). ENG-413 WP10: the engine's own contract, which this server is held to;
TandemApp keeps its own copy of this file for its contracts/dashboard-v1. The validator is
the subset of JSON Schema those files use -- `type` (one or a list, `integer` excluding booleans),
`properties`, `required`, `additionalProperties: false`, `items`, `enum`, `const`, and `$ref`
within a file or to a sibling file -- in the standard library, because the box has no pip. Exit 1
and one line per violation, with its JSON path, when the document does not validate.
"""

from __future__ import annotations

import json
import os
import sys

_TOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: the engine's contract
CONTRACT = os.path.join(_TOP, "contracts", "engine-api-v1")
#: the default folder: the engine's dashboard answers
ROOT = os.path.join(CONTRACT, "dashboard")

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def load(name: str, root: str = ROOT) -> dict:
    with open(os.path.join(root, name if name.endswith(".json") else f"{name}.schema.json")) as f:
        return json.load(f)


def _resolve(ref: str, doc: dict, root: str) -> tuple[dict, dict]:
    file, _, frag = ref.partition("#")
    base = load(file, root) if file else doc
    node = base
    for part in [p for p in frag.split("/") if p]:
        node = node[part]
    return node, base


def validate(value, schema: dict, *, root: str = ROOT, doc: dict | None = None,
             path: str = "$") -> list[str]:
    doc = doc if doc is not None else schema
    if "$ref" in schema:
        node, base = _resolve(schema["$ref"], doc, root)
        return validate(value, node, root=root, doc=base, path=path)
    errs = []
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](value) for x in types):
            return [f"{path}: expected {'/'.join(types)}, got {type(value).__name__} {value!r:.60}"]
    if "const" in schema and value != schema["const"]:
        errs.append(f"{path}: expected {schema['const']!r}, got {value!r:.60}")
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r:.60} is not one of {schema['enum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{path}: missing {k!r}")
        for k, v in value.items():
            if k in props:
                errs += validate(v, props[k], root=root, doc=doc, path=f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: unexpected {k!r}")
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            errs += validate(v, schema["items"], root=root, doc=doc, path=f"{path}[{i}]")
    return errs


def check(value, name: str, root: str = ROOT) -> list[str]:
    return validate(value, load(name, root), root=root)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    name = sys.argv[1]
    raw = open(sys.argv[2]).read() if len(sys.argv) > 2 else sys.stdin.read()
    root = ROOT
    if "/" in name:
        sub, _, name = name.rpartition("/")
        root = os.path.normpath(os.path.join(CONTRACT, sub))
    errs = check(json.loads(raw), name, root)
    for e in errs:
        print(e)
    print(f"{name}: {'valid' if not errs else f'{len(errs)} violations'}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
