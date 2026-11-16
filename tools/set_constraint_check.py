#!/usr/bin/env python3
"""SET_CONSTRAINT on a served model (ENG-458, engine-ext-v1 3.2): the act writes the tokens of the
request field it is the same as.

    set_constraint_check.py capture --port P --model M --out FILE
    set_constraint_check.py compare FILE

The engine under test runs the reference extension, which reads one request's constraints from a
body field and answers them as SET_CONSTRAINT at at_request:

    PYTHONPATH=contracts/engine-ext-v1 QSE_EXT=fake_ext \\
        QSE_EXT_ARGS='{"constraint_field": "x_set_constraint"}'

`capture` sends each case of PAIRS twice to that one engine: once with the constraint as the
request's own field (`structured_outputs`, `logit_bias`), once with the same constraint in
`x_set_constraint`, so both go through the same extension and differ only in where the constraint
came from. `compare` holds each pair's gate equal (tools/eng413_baseline.py `run_one`: the status,
the text, reasoning, calls and finish, the usage's counts, every logprobs entry's token, bytes and
exact log-probability, and the ids of the answer's text and of the prompt), and checks the CHECKS:
a tool_args act answers with a call to its function. The log lines `[ext] <id>: SET_CONSTRAINT`
say what the engine did with each act (carried out, gave way, refused).

A capture talks to 127.0.0.1 and refuses port 8000, the box's engine. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eng413_baseline as B  # noqa: E402

FIELD = "x_set_constraint"
PART = r"[0-9]{3}-[a-z]{2}"
PERSON = {"type": "object", "properties": {"name": {"type": "string", "maxLength": 24},
                                           "age": {"type": "integer"}}, "required": ["name", "age"]}
COLORS = ["red", "green", "blue"]


def _chat(content: str, think: bool, **extra) -> dict:
    body = {"messages": [{"role": "user", "content": content}], "temperature": 0, "logprobs": True,
            "cache_prompt": False, "max_tokens": 384 if think else 96}
    if not think:
        body.update(B.NO_THINK)
    body.update(extra)
    return body


def pairs(bias: dict) -> list:
    """(name, endpoint, the request with the field, the request with the act). `bias`: the logit
    bias case's {token id: value}, read from the engine's tokenizer at capture time."""
    out = []
    for stream in (False, True):
        tag = "_stream" if stream else ""
        out += [
            (f"regex_nothink{tag}", "/v1/chat/completions",
             _chat("Give a part number in the form 123-ab.", False, stream=stream,
                   structured_outputs={"regex": PART}),
             _chat("Give a part number in the form 123-ab.", False, stream=stream,
                   **{FIELD: [{"kind": "regex", "value": PART}]})),
            (f"choice_think{tag}", "/v1/chat/completions",
             _chat("Which colour is the sky on a clear day? One word.", True, stream=stream,
                   structured_outputs={"choice": COLORS}),
             _chat("Which colour is the sky on a clear day? One word.", True, stream=stream,
                   **{FIELD: [{"kind": "choice", "value": COLORS}]})),
            (f"json_nothink{tag}", "/v1/chat/completions",
             _chat("Describe a sailor as JSON.", False, stream=stream, structured_outputs={"json": PERSON}),
             _chat("Describe a sailor as JSON.", False, stream=stream,
                   **{FIELD: [{"kind": "json_schema", "value": PERSON}]})),
            (f"bias_nothink{tag}", "/v1/chat/completions",
             _chat("Name three rivers in Europe, one line each, nothing else.", False, stream=stream,
                   logit_bias=bias),
             _chat("Name three rivers in Europe, one line each, nothing else.", False, stream=stream,
                   **{FIELD: [{"kind": "logit_bias", "value": bias}]})),
        ]
    weather = "What is the weather in Berlin? Use the tool, or answer as JSON if you know it."
    out += [
        ("tools_or_json", "/v1/chat/completions",
         _chat(weather, False, tools=[B.WEATHER], structured_outputs={"json": PERSON}),
         _chat(weather, False, tools=[B.WEATHER], **{FIELD: [{"kind": "json_schema", "value": PERSON}]})),
        ("json_think", "/v1/chat/completions",
         _chat("Describe a sailor as JSON.", True, structured_outputs={"json": PERSON}),
         _chat("Describe a sailor as JSON.", True, **{FIELD: [{"kind": "json_schema", "value": PERSON}]})),
        ("completion_regex", "/v1/completions",
         {"prompt": "The part number on the crate is ", "max_tokens": 32, "temperature": 0, "logprobs": 1,
          "cache_prompt": False, "structured_outputs": {"regex": PART}},
         {"prompt": "The part number on the crate is ", "max_tokens": 32, "temperature": 0, "logprobs": 1,
          "cache_prompt": False, FIELD: [{"kind": "regex", "value": PART}]}),
        # precedence: the request's own response_format wins; the act gives way
        ("precedence", "/v1/chat/completions",
         _chat("Describe a sailor as JSON.", False, response_format={
             "type": "json_schema", "json_schema": {"name": "p", "schema": PERSON}}),
         _chat("Describe a sailor as JSON.", False, response_format={
             "type": "json_schema", "json_schema": {"name": "p", "schema": PERSON}},
             **{FIELD: [{"kind": "regex", "value": PART}]})),
        # an invalid constraint: the request runs as without it
        ("invalid", "/v1/chat/completions",
         _chat("Name three rivers in Europe, one line each, nothing else.", False),
         _chat("Name three rivers in Europe, one line each, nothing else.", False,
               **{FIELD: [{"kind": "regex", "value": "(a"}]})),
    ]
    return out


#: (name, the request with the act, the function the answer calls)
CHECKS = [("tool_args", _chat("What is the weather in Berlin?", False, tools=[B.WEATHER, B.BASH],
                              **{FIELD: [{"kind": "choice", "value": ["get_weather"], "scope": "tool_args"}]}),
           "get_weather")]


def _bias(port: int) -> dict:
    """-100 on the first id of each of three common words: the answer has to go around them."""
    out = {}
    for w in (" the", " The", " and"):
        r = B.request(port, "POST", "/v1/tokenize", {"text": w, "ids": True}, timeout=60)
        ids = (r.get("body") or {}).get("ids") or []
        if ids:
            out[str(ids[0])] = -100
    if not out:
        raise SystemExit("set_constraint_check: /v1/tokenize answered no ids")
    return out


def cmd_capture(a) -> int:
    B._guard(a.port)
    t0 = time.monotonic()
    bias = _bias(a.port)
    doc = {"set_constraint_check": 1, "port": a.port, "model": a.model, "bias": bias, "pairs": [],
           "checks": [], "captured": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    for name, endpoint, field, act in pairs(bias):
        f = B.run_one(a.port, f"{name}/field", endpoint, dict(field, model=a.model))
        x = B.run_one(a.port, f"{name}/act", endpoint, dict(act, model=a.model))
        doc["pairs"].append({"name": name, "field": f, "act": x})
    for name, body, fn in CHECKS:
        doc["checks"].append({"name": name, "want": fn,
                              "rec": B.run_one(a.port, name, "/v1/chat/completions", dict(body, model=a.model))})
    doc["seconds"] = round(time.monotonic() - t0, 1)
    with open(a.out, "w") as f:
        json.dump(doc, f, indent=1)
    print(f"[set_constraint] {len(doc['pairs'])} pairs, {len(doc['checks'])} checks, {doc['seconds']} s -> {a.out}")
    return 0


def verdicts(doc: dict) -> list:
    """(name, ok, why) for each pair and check of a capture."""
    out = []
    for p in doc["pairs"]:
        f, x = p["field"]["gate"], p["act"]["gate"]
        if f["status"] != 200 or x["status"] != 200 or f["wire"].get("error") or x["wire"].get("error"):
            out.append((p["name"], False, f"status {f['status']}/{x['status']}"))
            continue
        ds = B.diffs(f, x)
        out.append((p["name"], not ds, "; ".join(f"{d[0]} {d[1]}: {str(d[2])[:60]} != {str(d[3])[:60]}" for d in ds[:3]) if ds else
                    f"{sum(len(v) for v in f['logprobs'].values())} logprobs entries, finish {f['wire']['finish']}"))
    for c in doc["checks"]:
        w = c["rec"]["gate"]["wire"]
        names = [v["name"] for v in (w.get("calls") or {}).values()]
        ok = c["rec"]["gate"]["status"] == 200 and w.get("finish") == "tool_calls" and names == [c["want"]]
        out.append((c["name"], ok, f"finish {w.get('finish')}, calls {names}"))
    return out


def cmd_compare(a) -> int:
    with open(a.file) as f:
        doc = json.load(f)
    rows = verdicts(doc)
    for name, ok, why in rows:
        print(f"[set_constraint] {'SAME' if ok else 'DIFFERENT':<9} {name}: {why}")
    bad = [n for n, ok, _ in rows if not ok]
    print(f"[set_constraint] {len(rows) - len(bad)} of {len(rows)} hold" + (f"; failed {bad}" if bad else ""))
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("--port", type=int, required=True)
    c.add_argument("--model", required=True, help="the served raw model name")
    c.add_argument("--out", required=True)
    m = sub.add_parser("compare")
    m.add_argument("file")
    a = ap.parse_args(argv)
    return cmd_capture(a) if a.cmd == "capture" else cmd_compare(a)


if __name__ == "__main__":
    sys.exit(main())
