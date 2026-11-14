"""The JSON-schema subset the setup registry and the community index are checked with (ENG-413 WP2c).

`validate(value, schema)` answers every violation as one line with its JSON path, [] when the value
is valid: types, `const`, `enum`, number bounds, string lengths and patterns, an object's required
and unknown keys (`additionalProperties` false, or a schema every id-named extra key must meet) and
list items. A copy of the conductor's validator (TandemApp's tandemapp/conductor/schema.py
`validate`, `_TYPES`, `ID_RE`), which server/setup/registry.py and server/setup/community.py read
before the split; TandemApp keeps its own for the conductor's documents.
"""

from __future__ import annotations

import re

ID_RE = r"^[a-z][a-z0-9_-]{0,39}$"

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}

def validate(value, schema: dict, path: str = "$") -> list[str]:
    """Violations of `schema` by `value`, one line each with its JSON path; [] when valid."""
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](value) for x in types):
            return [f"{path}: expected {'/'.join(types)}, got {type(value).__name__}"]
    errs: list[str] = []
    if "const" in schema and value != schema["const"]:
        errs.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r:.60} is not one of {', '.join(map(str, schema['enum']))}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: {value} is below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: {value} is above {schema['maximum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errs.append(f"{path}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append(f"{path}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errs.append(f"{path}: {value!r:.60} does not match {schema['pattern']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{path}: missing {k!r}")
        extra = schema.get("additionalProperties", True)
        for k, v in value.items():
            if k in props:
                errs += validate(v, props[k], f"{path}.{k}")
            elif extra is False:
                errs.append(f"{path}: unknown key {k!r}")
            elif isinstance(extra, dict):
                if not re.search(ID_RE, str(k)):
                    errs.append(f"{path}: key {k!r} is not an id ({ID_RE})")
                errs += validate(v, extra, f"{path}.{k}")
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, v in enumerate(value):
                errs += validate(v, schema["items"], f"{path}[{i}]")
    return errs
