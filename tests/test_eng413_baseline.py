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
  3b a memory writer's row between two monitor rows shifts no gated ordinal; an id that
     moved still fails                                                    test_an_info_row_shifts_no_gated_ordinal
  3c B2 repeats on one tree: its requests go one after another, and the one that samples (the
     title) names its seed                                              test_b2_samples_only_with_a_seed
  3d --seeded-title: W0 drew the title's seed (TITLE_SEED), B2 now sends it, so the title's route
     row has no `extra.seed_drawn`: accepted by name on that row only, with the seed sent and
     the baseline's drawn seed TITLE_SEED; anything else there, or a moved token, still fails
                                                                    test_a_sent_title_seed_draws_none
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


def test_an_info_row_shifts_no_gated_ordinal():
    """A memory writer's row that one run writes between two monitor rows (its timer, W2a's dry run
    dry3) is info: the gated rows' ids keep their ordinals; an id that did move still fails."""
    tmp = tempfile.mkdtemp(prefix="eng413-ord-")
    try:
        ev = [{"id": "e1", "kind": "route", "decision": "PLAN", "request_id": "chatcmpl-aaaa1111",
               "session_key": "s-1"},
              {"id": "e2", "kind": "monitor", "task": "E1", "decision": "CONTINUE_HIDDEN",
               "request_id": "chatcmpl-aaaa1111", "hidden_text_id": "t1"},
              {"id": "e3", "kind": "monitor", "task": "E1", "decision": "GIVE_UP",
               "request_id": "chatcmpl-bbbb2222", "session_key": "s-1"}]
        rows = {"conductor_events": ev, "conductor_texts": [{"id": "t1", "text": "go on"}],
                "requests": [{"id": 1, "request_id": "chatcmpl-aaaa1111", "parent_request": None},
                             {"id": 2, "request_id": "chatcmpl-bbbb2222",
                              "parent_request": "chatcmpl-aaaa1111"}]}
        _write(os.path.join(tmp, "base"), _record(["a"], journal_rows=rows))
        extra = copy.deepcopy(rows)
        extra["conductor_events"].insert(1, {"id": "m9", "kind": "memory", "task": "writer",
                                             "decision": "block", "request_id": "chatcmpl-cccc3333"})
        _write(os.path.join(tmp, "extra"), _record(["a"], journal_rows=extra))
        rep = B.compare_dirs(os.path.join(tmp, "base"), os.path.join(tmp, "extra"))
        e = rep["files"]["journal-chat.json"]
        assert e["verdict"] == "SAME" and rep["gate_fail"] == 0, e
        assert len(e["info"]) == 1 and "other_events[0]" in e["info"][0], e
        moved = copy.deepcopy(extra)
        moved["requests"][1]["parent_request"] = "chatcmpl-bbbb2222"      # now its own id
        _write(os.path.join(tmp, "moved"), _record(["a"], journal_rows=moved))
        rep = B.compare_dirs(os.path.join(tmp, "base"), os.path.join(tmp, "moved"))
        e = rep["files"]["journal-chat.json"]
        assert e["verdict"] == "DIFF" and len(e["gate"]) == 1 and "parent_request" in e["gate"][0], e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return "an info row between monitor rows: gate SAME, info 1; a moved id: DIFF"


def test_b2_samples_only_with_a_seed():
    """B2 repeats on one tree: a request that samples (temperature above 0) names its seed, or the
    engine draws a fresh one per request (W0 and W2a's titles differed so); B2's requests go one
    after another, each after the last one's answer."""
    sent, orig = [], B.run_one

    def fake(port, name, endpoint, body, headers=None):
        sent.append((name, copy.deepcopy(body)))
        return {"name": name, "wire": {"content": "", "reasoning": "", "calls": {}}}
    B.run_one = fake
    try:
        B.battery_b2(1, 64)
    finally:
        B.run_one = orig
    names = [n for n, _ in sent]
    assert names == ["opencode_turn1", "opencode_turn2", "opencode_turn3", "hermes", "raw_header",
                     "opencode_title"], names
    sampled = [(n, b.get("seed")) for n, b in sent if float(b.get("temperature") or 0) > 0]
    assert sampled and all(isinstance(s, int) for _, s in sampled), sampled
    assert dict(sampled)["opencode_title"] == B.TITLE_SEED
    return f"{len(sent)} requests, {len(sampled)} sampled, each with its seed"


def _title_pair(seed_sent, drawn, token="t", rid="chatcmpl-ti7le001"):
    """B2's title request and its journal: a turn's route row, then the title's (found by its answer id)."""
    lp = [{"t": token, "b": list(token.encode()), "lp": -0.25}]
    req = {"model": "TandemLLM", "temperature": 0.5}
    if seed_sent is not None:
        req["seed"] = seed_sent
    title = {"name": "opencode_title", "status": 200, "request": req,
             "sse": ['data: {"id": "%s", "choices": [{"delta": {"content": "%s"}}]}' % (rid, token),
                     "data: [DONE]"],
             "gate": {"status": 200, "wire": {"content": token}, "usage": {}, "logprobs": {"content": lp}}}
    extra = {"session": "id"}
    if drawn is not None:
        extra["seed_drawn"] = drawn
    ev = [{"id": "e1", "kind": "route", "class_id": "code_edit", "request_id": "chatcmpl-turn0001",
           "extra": {"session": "id"}},
          {"id": "e2", "kind": "route", "class_id": "code_edit", "request_id": rid, "extra": extra}]
    return {"b2.json": {"eng413_baseline": 1, "set": "b2", "requests": [title]},
            "journal-chat.json": {"eng413_baseline": 1, "set": "journal",
                                  "rows": {"conductor_events": ev, "conductor_texts": [], "requests": []}}}


def test_a_sent_title_seed_draws_none():
    """W2c: W0's title named no seed, its engine drew TITLE_SEED and the title's route row wrote it;
    B2 sends TITLE_SEED since 019e3c1f, so nothing is drawn and the key is absent. --seeded-title
    accepts that absence by name, on that row, under those conditions only."""
    tmp = tempfile.mkdtemp(prefix="eng413-seed-")
    path = "journal-chat.route_monitor[1].extra.seed_drawn"
    try:
        _write(os.path.join(tmp, "base"), _title_pair(None, B.TITLE_SEED))

        def cmp(label, files, seeded=True):
            _write(os.path.join(tmp, label), files)
            return B.compare_dirs(os.path.join(tmp, "base"), os.path.join(tmp, label),
                                  seeded_title=seeded)
        sent = _title_pair(B.TITLE_SEED, None, rid="chatcmpl-ti7le002")
        rep = cmp("sent", sent, seeded=False)
        e = rep["files"]["journal-chat.json"]
        assert e["verdict"] == "DIFF" and e["gate"] == [f"{path} removed: {B.TITLE_SEED} -> null"], e
        rep = cmp("sent", sent)
        e = rep["files"]["journal-chat.json"]
        assert rep["gate_fail"] == 0 and e["verdict"] == "SAME", rep
        assert e["allowed"] == [f"{path} removed (seeded title: TITLE_SEED sent, the baseline drew it)"], e
        assert B.main(["compare", os.path.join(tmp, "base"), os.path.join(tmp, "sent"), "--seeded-title"]) == 0
        assert B.main(["compare", os.path.join(tmp, "base"), os.path.join(tmp, "sent")]) == 1
        # the rule holds nothing else: each of these still fails
        bad = {
            "drew another": _title_pair(B.TITLE_SEED, 4493622033207065211),
            "sent no seed": _title_pair(None, None),
            "sent another seed": _title_pair(B.TITLE_SEED + 1, None),
            "a moved token": _title_pair(B.TITLE_SEED, None, token="u"),
        }
        other = copy.deepcopy(sent)
        other["journal-chat.json"]["rows"]["conductor_events"][0]["extra"]["seed_drawn"] = 7
        bad["another row drew"] = other
        changed = copy.deepcopy(sent)
        changed["journal-chat.json"]["rows"]["conductor_events"][1]["extra"]["session"] = "new"
        bad["the row moved too"] = changed
        for i, (why, files) in enumerate(bad.items()):
            rep = cmp(f"bad{i}", files)
            assert rep["gate_fail"] >= 1, (why, rep)
        # a baseline that drew another seed than the one sent: not the pinned seed, no acceptance
        _write(os.path.join(tmp, "base2"), _title_pair(None, 4493622033207065211))
        rep = B.compare_dirs(os.path.join(tmp, "base2"), os.path.join(tmp, "sent"), seeded_title=True)
        assert rep["files"]["journal-chat.json"]["verdict"] == "DIFF", rep
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return f"accepted {path} only with TITLE_SEED sent and drawn; {len(bad) + 1} other cases fail"


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
    if config not in ("-", "off"):
        env["QSE_EXT"] = "server.ext_app"               # ENG-413 WP3a: chat mode's extension
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
    strip = ["--strip", os.path.join(tmp, label)]       # N8: this run's folder in the paths it reports
    try:
        if mode == "engine":
            for name, kind in (("b3-pre", "b3"), ("b1", "b1"), ("b3-post", "b3")):
                assert B.main(["capture", "--port", str(port), "--set", kind, "--out", out, "--name", name,
                               *strip]) == 0
        else:
            model = "TandemLLM" if mode == "chat" else "qwen38-spark-engine"
            assert B.main(["capture", "--port", str(port), "--set", "b2f", "--out", out, "--name", f"b2f-{mode}",
                           "--model", model, *strip]) == 0
    finally:
        _stop(p)
    assert B.main(["journal", "--ledger", ledger, "--out", out, "--name", f"journal-{mode}", *strip]) == 0
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
                bad = [r["name"] for r in recs if not r["gate"]["logprobs"]
                       or not isinstance(r["gate"].get("text_ids"), list) or not r["gate"].get("prompt_ids")]
                assert not bad, f"records without tokens, text ids or prompt ids: {bad}"
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
