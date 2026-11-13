"""The tool-call fixtures' runner (engine API 1.3, ENG-427): what "a parser passes toolcalls/" means.

The engine reads a model's tool calls out of its text for /v1/chat/completions and /v1/responses,
and TandemApp's conductor reads the same text; until each side has its own parser (ENG-413 WP2a)
both run one, after it two copies that must agree. Each `<form>.json` here holds cases in one of
the forms (`toolformats`: xml, argkey, pythonic), `repair.json` the argument renames and tool
mappings of `callrepair`. A case is the model's text and how the request offered its tools; `want`
is what the parser answers: the content around the calls, each call's name and its arguments as
the exact JSON text the client receives, the renames, and the same read as a stream in pieces.

Standard library only; it imports neither tree. A side passes its own parser in `api`:

    parse_tool_calls(text, names, eos, types) -> (content, calls)
    get_format(name) -> a ToolFormat (rewrite, stream_rewriter)
    ToolCallBuffer(names=, types=, fmt=, schemas=) -> feed, drain_deltas, finish, calls
    repair_calls(calls, schemas, types) -> (calls, renames)
    schemas_of(tools) -> {name: parameters}
    schema_types(tools) -> {name: {parameter: kinds}}

    failures = check.run(path, api)        [] when every case answers its `want`
    check.write(path, api)                 fills `want` from this parser (the engine's side only)
"""

from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ("xml.json", "argkey.json", "pythonic.json", "repair.json")


def _kinds(types):
    """A case's `types` ({fn: {param: [kind, ...]}}) as the parser takes them (frozensets)."""
    if not types:
        return None
    return {fn: {p: frozenset(k) for p, k in params.items()} for fn, params in types.items()}


def _calls(calls) -> list:
    return [{"name": c["function"]["name"], "arguments": c["function"]["arguments"]} for c in calls]


def answer(doc: dict, case: dict, api: dict) -> dict:
    """What this parser answers for one case."""
    fmt = api["get_format"](doc["form"])
    tools = doc["tool_sets"].get(case["tools"]) if case.get("tools") else None
    names = [t["function"]["name"] for t in tools] if (tools and case.get("names")) else None
    if case.get("types") == "schema":
        types = api["schema_types"](tools)
    else:
        types = _kinds(case.get("types"))
    schemas = api["schemas_of"](tools) if (tools and case.get("repair")) else None
    eos = bool(case.get("eos"))
    content, calls = api["parse_tool_calls"](fmt.rewrite(case["text"], eos=eos), names=names,
                                             eos=eos, types=types)
    renames = []
    if schemas:
        calls, renames = api["repair_calls"](calls, schemas, types)
    out = {"content": content, "calls": _calls(calls), "renames": renames}
    streams = []
    text = case["text"]
    for size in case.get("stream", ()):
        size = size or len(text) or 1
        b = api["ToolCallBuffer"](names=names, types=types, fmt=doc["form"], schemas=schemas)
        rw = fmt.stream_rewriter()
        got = ""
        for i in range(0, len(text), size):
            piece = text[i:i + size]
            got += "".join(b.feed(rw.feed(piece) if rw is not None else piece))
            b.drain_deltas()
        if rw is not None:
            got += "".join(b.feed(rw.finish(eos=eos)))
        left, _ = b.finish(eos=eos)
        streams.append({"content": got + left, "calls": _calls(b.calls)})
    if streams:
        first = streams[0]
        out["stream"] = first if all(s == first for s in streams) else {"by_size": streams}
    return out


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run(path: str, api: dict) -> list[str]:
    """One line per case whose answer is not its `want`."""
    doc = load(path)
    bad = []
    for case in doc["cases"]:
        try:
            got = answer(doc, case, api)
        except Exception as exc:                                   # noqa: BLE001
            bad.append(f"{os.path.basename(path)} {case['id']}: raised {exc!r}")
            continue
        if got != case.get("want"):
            keys = sorted(k for k in set(got) | set(case.get("want") or {})
                          if got.get(k) != (case.get("want") or {}).get(k))
            bad.append(f"{os.path.basename(path)} {case['id']}: {keys} differ: got "
                       f"{json.dumps({k: got.get(k) for k in keys})[:400]}")
    return bad


def write(path: str, api: dict) -> int:
    """Fill every case's `want` from this parser; returns the number of cases."""
    doc = load(path)
    for case in doc["cases"]:
        case["want"] = answer(doc, case, api)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    return len(doc["cases"])
