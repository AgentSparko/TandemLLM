"""tools/eng413_baseline.py: the B1/B2/B3 records, their normaliser and their compare (ENG-413, QA ENG-414).

The plan's gates G2-G4 hold a refactored engine's answers equal to these records, so the tool must
see every token that moved and must not see what moves on its own (ids, clocks, memory figures).

The test matrix:

  1  the normaliser: moments, durations, rates and memory figures become "<v>", host and GPU
     subtrees keep their keys only, ids become ordinals that keep references equal    test_the_normaliser
  2  /metrics becomes its family list (names, types, label names, no values)         test_metric_families
  3  compare: two equal folders pass; a token that moved fails with its index; a stream's
     chunks that moved are info only; --allow and --additive accept what they name;
     a journal row of the monitor that moved fails, a memory writer's row is info    test_compare_sees_tokens_not_noise
  4  a capture refuses port 8000 (the box's engine) before it opens a socket          test_the_live_port_is_refused
  5  over a socket, the fake engine: B1 and B3 from two fresh engines compare equal;
     the monitor fixtures (B2f) make D1, E1, GIVE_UP, C3 and C4 act, and two fresh
     engines journal them at the same tokens                                          test_fake_engine_records_repeat

Run: python tests/test_eng413_baseline.py   (5 needs CPU torch: the fake engine)
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools import eng413_baseline as B  # noqa: E402

LAUNCH = ("import sys, runpy; sys.modules['triton'] = None; sys.modules['triton.language'] = None;"
          "sys.argv = ['server/app.py'] + sys.argv[1:]; runpy.run_path('server/app.py', run_name='__main__')")


def test_the_normaliser():
    doc = {"id": "chatcmpl-abc123def", "created": 1794581471, "uptime_s": 12.5, "model": "m",
           "usage": {"prompt_tokens": 9, "queue_ms": 3.2, "decode_tps": 41.0},
           "memory": {"free_gib": 40.1, "rows": [1, 2, 3]}, "gpu": [{"util": 97, "name": "x"}],
           "choices": [{"message": {"tool_calls": [{"id": "call_q1w2e3r4", "function": {"name": "bash"}}]}}],
           "refs": ["call_q1w2e3r4", "call_zzzz9999"], "seed": 530527837667061159, "ms": 1.5,
           "tokens_at_cut": 2112, "position": 0}
    got = B.normalize(copy.deepcopy(doc))
    assert got["created"] == got["uptime_s"] == got["seed"] == got["ms"] == "<v>"
    assert got["usage"] == {"prompt_tokens": 9, "queue_ms": "<v>", "decode_tps": "<v>"}, got["usage"]
    assert got["memory"] == {"free_gib": "<v>", "rows": ["<v>"]}, got["memory"]
    assert got["gpu"] == [{"name": "<v>", "util": "<v>"}], got["gpu"]
    call_id = got["choices"][0]["message"]["tool_calls"][0]["id"]
    assert got["id"].startswith("#") and call_id.startswith("#") and got["id"] != call_id, got
    assert got["refs"][0] == call_id and got["refs"][1] not in (call_id, got["id"]), got["refs"]
    assert got["tokens_at_cut"] == 2112 and got["position"] == 0 and got["model"] == "m"
    assert B.normalize(doc) == B.normalize(doc), "normalising is a function of the input"
    other = dict(doc, id="chatcmpl-zzz999yyy", created=1, uptime_s=99.0)
    assert B.normalize(other) == got, "two answers that differ only in ids and clocks"


def test_metric_families():
    text = ("# HELP qse_requests_total requests\n# TYPE qse_requests_total counter\n"
            'qse_requests_total{endpoint="chat",status="200"} 4\n'
            "# TYPE qse_ttft_seconds histogram\n"
            'qse_ttft_seconds_bucket{le="0.5"} 1\nqse_ttft_seconds_sum 0.3\nqse_ttft_seconds_count 1\n'
            "# TYPE qse_up gauge\nqse_up 1\n")
    fams = B.metric_families(text)
    assert fams == [["qse_requests_total", "counter", ["endpoint", "status"]],
                    ["qse_ttft_seconds", "histogram", []], ["qse_up", "gauge", []]], fams
    assert B.metric_families(text.replace(" 4\n", " 5\n")) == fams, "values are not compared"


def _record(tokens, chunks_note="", journal_rows=None):
    lp = [{"t": t, "b": list(t.encode()), "lp": -0.5} for t in tokens]
    rec = {"name": "r1", "status": 200, "sse": [
        'data: {"id": "chatcmpl-aaaa1111", "created": 1, "choices": [{"delta": {"content": "%s"}}]}'
        % ("".join(tokens) + chunks_note), "data: [DONE]"],
        "gate": {"status": 200, "wire": {"content": "".join(tokens), "finish": "stop"}, "usage": {},
                 "logprobs": {"content": lp}, "text_ids": list(range(len(tokens)))}}
    out = {"b1.json": {"eng413_baseline": 1, "set": "b1", "requests": [rec]}}
    if journal_rows is not None:
        out["journal-chat.json"] = {"eng413_baseline": 1, "set": "journal", "rows": journal_rows}
    return out


def _write(d, files):
    os.makedirs(d, exist_ok=True)
    for name, doc in files.items():
        with open(os.path.join(d, name), "w") as f:
            json.dump(doc, f)


def test_compare_sees_tokens_not_noise():
    tmp = tempfile.mkdtemp(prefix="eng413-cmp-")
    try:
        rows = {"conductor_events": [
            {"id": "e1", "ts_ms": 1, "kind": "monitor", "task": "D1", "decision": "CLOSE_REASONING",
             "tokens_at_cut": 2112, "request_id": "chatcmpl-r1xxxx"},
            {"id": "e2", "ts_ms": 2, "kind": "memory", "task": "transcript", "decision": "block"}],
            "conductor_texts": [], "requests": [{"id": 1, "ts_ms": 5, "request_id": "chatcmpl-r1xxxx",
                                                 "completion_tokens": 3, "draft_tokens": 7}]}
        base = _record(["a", "b", "c"], journal_rows=rows)
        _write(os.path.join(tmp, "base"), base)
        same = copy.deepcopy(base)
        same["journal-chat.json"]["rows"]["conductor_events"][0].update(id="zz", ts_ms=99,
                                                                         request_id="chatcmpl-r2yyyy")
        same["journal-chat.json"]["rows"]["requests"][0].update(ts_ms=7, request_id="chatcmpl-r2yyyy",
                                                                draft_tokens=9)
        _write(os.path.join(tmp, "same"), same)
        rep = B.compare_dirs(os.path.join(tmp, "base"), os.path.join(tmp, "same"))
        assert rep["gate_fail"] == 0 and rep["info_diff"] == 0, rep
        # a token moved: the gate fails and names token 1
        moved = _record(["a", "x", "c"], journal_rows=rows)
        _write(os.path.join(tmp, "moved"), moved)
        rep = B.compare_dirs(os.path.join(tmp, "base"), os.path.join(tmp, "moved"))
        assert rep["gate_fail"] == 1 and rep["files"]["b1.json"]["verdict"] == "DIFF", rep
        assert "token 1 of 3/3" in rep["files"]["b1.json"]["gate"][0], rep["files"]["b1.json"]
        # only the stream's chunks moved: info, no failure
        chunk = copy.deepcopy(base)
        chunk["b1.json"]["requests"][0]["sse"][0] = chunk["b1.json"]["requests"][0]["sse"][0].replace(
            '"content"', '"role": "assistant", "content"')
        _write(os.path.join(tmp, "chunk"), chunk)
        rep = B.compare_dirs(os.path.join(tmp, "base"), os.path.join(tmp, "chunk"))
        assert rep["gate_fail"] == 0 and rep["info_diff"] == 1, rep
        # the monitor's row moved: gate; a memory writer's row moved: info
        j = copy.deepcopy(base)
        j["journal-chat.json"]["rows"]["conductor_events"][0]["tokens_at_cut"] = 2113
        j["journal-chat.json"]["rows"]["conductor_events"][1]["decision"] = "unknown"
        _write(os.path.join(tmp, "journal"), j)
        rep = B.compare_dirs(os.path.join(tmp, "base"), os.path.join(tmp, "journal"))
        e = rep["files"]["journal-chat.json"]
        assert e["verdict"] == "DIFF" and len(e["gate"]) == 1 and "tokens_at_cut" in e["gate"][0], e
        assert len(e["info"]) == 1 and "decision" in e["info"][0], e
        # --allow names the path; --additive takes a field only the candidate has
        rep = B.compare_dirs(os.path.join(tmp, "base"), os.path.join(tmp, "journal"),
                             allow=["journal-chat.route_monitor[0].tokens_at_cut"])
        assert rep["files"]["journal-chat.json"]["verdict"] == "SAME", rep
        add = copy.deepcopy(base)
        add["journal-chat.json"]["rows"]["conductor_events"][0]["new_field"] = 1
        _write(os.path.join(tmp, "add"), add)
        assert B.compare_dirs(os.path.join(tmp, "base"), os.path.join(tmp, "add"))["gate_fail"] == 1
        assert B.compare_dirs(os.path.join(tmp, "base"), os.path.join(tmp, "add"), additive=True)["gate_fail"] == 0
        # a record missing in the candidate fails
        os.unlink(os.path.join(tmp, "add", "b1.json"))
        rep = B.compare_dirs(os.path.join(tmp, "base"), os.path.join(tmp, "add"), additive=True)
        assert rep["files"]["b1.json"]["verdict"] == "MISSING" and rep["gate_fail"] == 1, rep
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_live_port_is_refused():
    for argv in (["capture", "--port", "8000", "--set", "b3", "--out", "/nonexistent"],
                 ["capture", "--port", "8000", "--set", "b1", "--out", "/nonexistent", "--model", "m"]):
        try:
            B.main(argv)
        except SystemExit as e:
            assert "8000" in str(e.code), e.code
        else:
            raise AssertionError("port 8000 was not refused")
    try:
        B.request(8000, "GET", "/health")
    except SystemExit:
        pass
    else:
        raise AssertionError("request() to port 8000 was not refused")


# ---------------------------------------------------------------------------- over a socket

def _free_port() -> int:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ports import free_port
    return free_port()


def _fake(port: int, ledger: str, config: str):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.path.dirname(ledger),
           "PYTHONPATH": ROOT, "CUDA_VISIBLE_DEVICES": "", "TZ": "Europe/Berlin"}
    p = subprocess.Popen([sys.executable, "-c", LAUNCH, "--fake-engine", "--host", "127.0.0.1", "--port", str(port),
                          "--served-model", "qwen38-spark-engine", "--usage-ledger", ledger, "--fake-tps", "4000",
                          "--conductor", "on" if config != "-" else "raw",
                          "--conductor-config", config if config != "-" else "off",
                          "--conductor-defaults", "shipped", "--disk-cache", "off", "--session-spill", "off"],
                         cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    end = time.time() + 90
    while time.time() < end:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            return p
        except OSError:
            if p.poll() is not None:
                raise AssertionError(p.stdout.read()[-3000:])
            time.sleep(0.3)
    p.kill()
    raise AssertionError("the fake engine did not answer /health in 90 s")


def _stop(p) -> None:
    p.terminate()
    try:
        p.wait(30)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait(10)


def _run(tmp: str, label: str, mode: str) -> str:
    """One fresh fake engine: mode engine (B1, B3), chat or monitor (B2f); the records' folder."""
    out = os.path.join(tmp, label, "records")
    wd = os.path.join(tmp, label, "work")
    os.makedirs(wd, exist_ok=True)
    config = {"engine": "-", "chat": os.path.join(wd, "conductor"), "monitor": "off"}[mode]
    if mode == "chat":
        os.makedirs(config)
    ledger = os.path.join(wd, "ledger.sqlite3")
    port = _free_port()
    p = _fake(port, ledger, config)
    try:
        if mode == "engine":
            assert B.main(["capture", "--port", str(port), "--set", "b3", "--out", out, "--name", "b3-pre"]) == 0
            assert B.main(["capture", "--port", str(port), "--set", "b1", "--out", out, "--name", "b1"]) == 0
            assert B.main(["capture", "--port", str(port), "--set", "b3", "--out", out, "--name", "b3-post"]) == 0
        else:
            model = "TandemLLM" if mode == "chat" else "qwen38-spark-engine"
            assert B.main(["capture", "--port", str(port), "--set", "b2f", "--out", out, "--name", f"b2f-{mode}",
                           "--model", model]) == 0
    finally:
        _stop(p)
    assert B.main(["journal", "--ledger", ledger, "--out", out, "--name", f"journal-{mode}"]) == 0
    return out


def _acts(path: str) -> dict:
    with open(path) as f:
        rows = json.load(f)["rows"]["conductor_events"]
    out = {}
    for r in rows:
        if r.get("kind") == "monitor" and r.get("mode") == "act":
            out[f"{r['task']}:{r['decision']}"] = out.get(f"{r['task']}:{r['decision']}", 0) + 1
    return out


def test_fake_engine_records_repeat():
    try:
        import torch  # noqa: F401
    except ImportError:
        from scriptmode import Skip
        raise Skip("no torch here: the fake engine needs CPU torch")
    tmp = tempfile.mkdtemp(prefix="eng413-fake-")
    try:
        words = []
        for mode in ("engine", "chat", "monitor"):
            a, b = _run(tmp, f"{mode}-1", mode), _run(tmp, f"{mode}-2", mode)
            rep = B.compare_dirs(a, b)
            bad = {f: e for f, e in rep["files"].items() if e["verdict"] != "SAME"}
            assert not bad and rep["gate_fail"] == 0, json.dumps(bad, indent=1)[:3000]
            words.append(f"{mode}: {len(rep['files'])} records equal")
            if mode == "engine":
                with open(os.path.join(a, "b1.json")) as f:
                    recs = json.load(f)["requests"]
                assert all(r["gate"]["logprobs"] and r["gate"]["text_ids"] for r in recs), \
                    [r["name"] for r in recs if not r["gate"]["logprobs"]]
            else:
                acts = _acts(os.path.join(a, f"journal-{mode}.json"))
                want = {"D1:CLOSE_REASONING", "E1:CONTINUE_HIDDEN", "E1:GIVE_UP", "C3:REGENERATE_CALL"}
                if mode == "monitor":
                    want.add("C4:REGENERATE_CALL")
                assert want <= set(acts), (mode, acts)
                words.append(f"{mode} acts {sorted(acts)}")
        return "; ".join(words)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
