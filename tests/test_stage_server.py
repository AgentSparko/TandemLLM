"""ENG-308: a stage process as the server starts it (server/stage.py), no GPU.

  -  the flags: `--stage LO:HI`, `--link-listen`, `--link-peer`, `--link-key-file`; without
     `--stage` nothing is set, and a link flag alone is refused
  -  the key file: a regular file of this user, mode 0600, at least 32 bytes; anything else is
     refused, and no message carries a byte of the key
  -  refusals before any load: both or neither of listen and peer, a name or a wildcard for an
     address, more than one slot, the KV pool, the disk tier, the conductor, backends, setup mode,
     a range that is neither the front nor the tail
  -  two stages over a link: the front answers `/health` with its stage and 409 on every other
     route; the tail's blocks cross the link; `/health`'s `stage` block names the range, the
     link and the options
  -  two sides that disagree are refused with the reason: ranges that do not meet, another code,
     other weights, another key; a tail without a front gives up after its wait
  -  a stalled link ends the block in flight with `stage_down` inside the deadline, then the
     tail is `degraded`, a request gets 503 with Retry-After, and after the heal a new link
     starts under a new epoch; a cut link the same
  -  the identity block's `stage` field names the range; a request with images gets 400
  -  two server processes on the fake engine (CPU torch): the tail's completion is the one a
     plain fake engine writes, its identity names its stage, the front serves no model route, a
     killed front ends in 503 `stage_down` and a new front is taken under a new epoch; the key is
     in no log line

Run: python tests/test_stage_server.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from engine.methods import link as L  # noqa: E402
from engine.methods import stages as S  # noqa: E402
from server import identity as identity_mod  # noqa: E402
from server import stage  # noqa: E402

KEY = "k3y-" + "x7" * 20


def _tmp() -> str:
    return tempfile.mkdtemp(prefix="stage-server-")


def _keyfile(d: str, text: str = KEY, mode: int = 0o600, name: str = "link.key") -> str:
    p = os.path.join(d, name)
    with open(p, "w") as f:
        f.write(text + "\n")
    os.chmod(p, mode)
    return p


def _args(*argv) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake-engine", action="store_true")
    ap.add_argument("--model", default=None)
    ap.add_argument("--parallel", type=int, default=1)
    ap.add_argument("--kv-pool", default="off")
    ap.add_argument("--disk-cache", default="off")
    ap.add_argument("--conductor", default="raw")
    ap.add_argument("--backends", default="off")
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--max-len", type=int, default=8192)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--served-model", default="m")
    stage.add_flags(ap)
    return ap.parse_args(["--fake-engine", *argv])


def _refused(fn, *needles) -> str:
    try:
        fn()
    except (SystemExit, ValueError) as e:
        text = str(e)
        for n in needles:
            assert n in text, (n, text)
        assert KEY not in text and KEY[:12] not in text, "a refusal quotes the key"
        return text
    raise AssertionError(f"not refused (wanted {needles})")


def _prepared(*argv, log=None) -> dict:
    state: dict = {}
    stage.prepare(_args(*argv), state, log=log or (lambda line: None))
    return state


# ------------------------------------------------------------------ flags and the key file
def test_the_flags_and_the_range():
    a = _args()
    assert (a.stage, a.link_listen, a.link_peer, a.link_key_file) == ("", "", "", "")
    assert _prepared() == {}, "without --stage nothing is set"
    assert stage.parse_range("0:12") == (0, 12) and stage.parse_range("12:24") == (12, 24)
    for bad in ("", "12", "a:b", "3:3", "5:2", "-1:4", "1:2:3", "0:"):
        _refused(lambda bad=bad: stage.parse_range(bad), "LO:HI")
    d = _tmp()
    key = _keyfile(d)
    for flag, val in (("--link-listen", "127.0.0.1:9100"), ("--link-peer", "127.0.0.1:9100"),
                      ("--link-key-file", key)):
        _refused(lambda: _prepared(flag, val), "needs --stage")
    return "four flags; a link flag without --stage is refused"


def test_the_key_file_rules():
    d = _tmp()
    good = _keyfile(d)
    assert stage.read_key_file(good) == KEY.encode()
    assert stage.read_key_file(_keyfile(d, "  " + KEY + "  \n", name="padded")) == KEY.encode()
    _refused(lambda: stage.read_key_file(""), "--link-key-file is required")
    _refused(lambda: stage.read_key_file(os.path.join(d, "none")), "cannot be read")
    for mode in (0o644, 0o640, 0o604, 0o400, 0o700, 0o666):
        p = _keyfile(d, mode=mode, name=f"m{mode:o}")
        try:
            _refused(lambda: stage.read_key_file(p), "must be 0600", f"{mode:04o}")
        finally:
            os.chmod(p, 0o600)
    _refused(lambda: stage.read_key_file(_keyfile(d, "short-key-of-31-bytes-123456789", name="short")),
             "shorter than 32")
    _refused(lambda: stage.read_key_file(_keyfile(d, " " * 64, name="blank")), "shorter than 32")
    os.symlink(good, os.path.join(d, "link"))
    _refused(lambda: stage.read_key_file(os.path.join(d, "link")), "cannot be read")
    os.mkdir(os.path.join(d, "dir"), 0o700)
    _refused(lambda: stage.read_key_file(os.path.join(d, "dir")))
    return "0600, a regular file of this user, 32 bytes; no refusal quotes the key"


def test_what_a_stage_does_not_serve_is_refused_before_any_load():
    d = _tmp()
    key = _keyfile(d)
    ok = ("--stage", "0:2", "--link-listen", "127.0.0.1:9100", "--link-key-file", key)
    lines = []
    st = _prepared(*ok, log=lines.append)["stage"]
    assert (st.role, st.lo, st.hi, st.layers) == ("front", 0, 2, stage.FAKE_LAYERS)
    assert st.ident == {"role": "front", "lo": 0, "hi": 2} and "Stage(front [0, 2) of 4)" == repr(st)
    assert any("front stage, layers [0, 2) of 4" in x for x in lines) and KEY not in "".join(lines)
    tail = _prepared("--stage", "2:4", "--link-peer", "127.0.0.1:9100", "--link-key-file", key)["stage"]
    assert tail.role == "tail" and tail.ident == {"role": "tail", "lo": 2, "hi": 4}
    base = ("--stage", "0:2", "--link-key-file", key)
    _refused(lambda: _prepared(*base), "one of --link-listen and --link-peer")
    _refused(lambda: _prepared(*base, "--link-listen", "127.0.0.1:1", "--link-peer", "127.0.0.1:2"), "one of")
    _refused(lambda: _prepared(*base, "--link-listen", "0.0.0.0:9100"), "every interface")
    _refused(lambda: _prepared(*base, "--link-listen", "localhost:9100"))
    _refused(lambda: _prepared(*base, "--link-peer", "front.example:9100"))
    _refused(lambda: _prepared("--stage", "0:2", "--link-listen", "127.0.0.1:9100"), "--link-key-file is required")
    for extra, why in ((("--parallel", "2"), "one slot"), (("--kv-pool", "on"), "--kv-pool off"),
                       (("--disk-cache", "/tmp/x"), "--disk-cache off"), (("--conductor", "on"), "--conductor raw"),
                       (("--backends", "/tmp/b"), "--backends off"), (("--setup",), "setup mode")):
        _refused(lambda extra=extra: _prepared(*ok, *extra), why)
    for rng in ("0:4", "1:3", "0:5", "2:6", "4:5"):
        _refused(lambda rng=rng: _prepared("--stage", rng, *ok[2:]), "front 0:k or the tail k:4")
    _refused(lambda: _prepared("--stage", "x", *ok[2:]), "LO:HI")
    return "one address, a literal one; one slot, no pool, no disk tier, raw; the front or the tail only"


# ------------------------------------------------------------------ two stages over a link
def _free_port() -> int:
    from ports import free_port
    return free_port()


def _http(port: int, path: str, body=None, method=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, dict(r.headers), json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read() or b"{}")


def _front(key: str, k: int = 2, **over):
    """A front stage in this process: (its Stage, its HTTP port, its link port)."""
    link_port, port = _free_port(), _free_port()
    a = _args("--stage", f"0:{k}", "--link-listen", f"127.0.0.1:{link_port}", "--link-key-file", key,
              "--port", str(port))
    state: dict = {}
    stage.prepare(a, state, log=lambda line: None)
    st = state["stage"]
    st._base.update(over)
    st.log = lambda line: None
    threading.Thread(target=stage.serve_front, args=(a, state, lambda line: None), daemon=True).start()
    end = time.monotonic() + 10
    while time.monotonic() < end:
        try:
            if _http(port, "/health")[0] == 200:
                break
        except OSError:
            time.sleep(0.05)
    return st, port, link_port


def _tail(key: str, to: int, k: int = 2, **over):
    state = _prepared("--stage", f"{k}:4", "--link-peer", f"127.0.0.1:{to}", "--link-key-file", key)
    st = state["stage"]
    st._base.update(over)
    st.log = lambda line: None
    return state, st


def test_two_stages_over_a_link():
    d = _tmp()
    key = _keyfile(d)
    front, port, link_port = _front(key)
    state, tail = _tail(key, link_port)
    tick = tail.fake_tail()
    for n in (1, 4, 7):
        tick(list(range(10, 10 + n)))
    tick([])
    co = tail.coordinator
    assert co.length == 12 and co.probe()["length"] == 12 and co.epoch == 1
    h = stage.health(state, "ok")
    assert "status" not in h, h
    s = h["stage"]
    assert (s["role"], s["lo"], s["hi"], s["layers"], s["cut"]) == ("tail", 2, 4, 4, 2), s
    assert s["link"]["state"] == "up" and s["link"]["epoch"] == 1 and s["link"]["transport"] == "tcp"
    assert s["link"]["bytes"]["rx"] > 12 * 4 and s["options"] == {"slots": 1}
    code, _, body = _http(port, "/health")
    f = body["stage"]
    assert code == 200 and body["status"] == "ok" and (f["role"], f["lo"], f["hi"]) == ("front", 0, 2), body
    assert f["link"]["state"] == "up" and f["steps"] == 3, f
    for path, method, payload in (("/v1/models", None, None), ("/v1/replica/identity", None, None),
                                  ("/v1/completions", "POST", {"prompt": "hi"}),
                                  ("/v1/chat/completions", "POST", {"messages": []}), ("/health", "POST", {})):
        code, _, body = _http(port, path, payload, method)
        assert code == 409 and body["error"]["code"] == "stage_front", (path, code, body)
    assert stage.refusal(state, {"prompt": "x"}) is None and stage.front(state) is False
    assert stage.front({"stage": front}) is True and stage.front({}) is False
    assert KEY not in json.dumps(h) + json.dumps(body)
    return "blocks of 1, 4 and 7 rows crossed; both /health name the stage; the front answers 409 elsewhere"


def test_sides_that_disagree_are_refused_with_the_reason():
    d = _tmp()
    key = _keyfile(d)
    other = _keyfile(d, "another-key-" + "z" * 30, name="other.key")
    saved, stage.ATTACH_S = stage.ATTACH_S, 3.0
    try:
        _, _, link_port = _front(key, k=1)
        _, tail = _tail(key, link_port, k=2)                # [0, 1) and [2, 4) do not meet
        _refused(lambda: tail.fake_tail(), "refused the link (stage)")
        for over, reason in (({"code": "another-build"}, "code"), ({"weights": "another-pack"}, "weights")):
            _, _, link_port = _front(key, **over)
            _, tail = _tail(key, link_port)
            _refused(lambda: tail.fake_tail(), f"refused the link ({reason})")
        _, _, link_port = _front(other)
        _, tail = _tail(key, link_port)
        _refused(lambda: tail.fake_tail(), "refused the link (auth)")
        _, tail = _tail(key, _free_port())                  # nobody listens there
        t0 = time.monotonic()
        _refused(lambda: tail.fake_tail(), "no front stage at 127.0.0.1:", "inside 3 s")
        assert 2.5 < time.monotonic() - t0 < 10
        # an adapter of another range than the flags name
        _refused(lambda: tail.identity(stage.FakeStage(1, 4)), "not --stage 2:4")
    finally:
        stage.ATTACH_S = saved
    return "stage, code, weights, auth: each named; a tail without a front gives up"


def _wait(fn, seconds: float, what: str):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.05)
    raise AssertionError(f"{what}: not inside {seconds:g} s")


def test_a_stalled_and_a_cut_link_end_in_stage_down_and_heal():
    from test_linkrelay import Relay
    d = _tmp()
    key = _keyfile(d)
    front, port, link_port = _front(key)
    r = Relay(link_port)
    state, tail = _tail(key, r.port)
    try:
        tick = tail.fake_tail()
        co = tail.coordinator
        co.step_deadline = 1.0
        tick([1, 2, 3])
        took = {}
        for fault in ("blackhole", "cut"):
            epoch = co.epoch
            r.cmd(cmd=fault)
            t0 = time.monotonic()
            try:
                tick([4, 5])
                raise AssertionError(f"{fault}: the block went through")
            except S.StageDown as e:
                assert e.code == "stage_down"
            took[fault] = time.monotonic() - t0
            assert took[fault] < 3.0, took
            h = stage.health(state, "ok")
            assert h["status"] == "degraded" and h["stage"]["link"]["state"] == "down", h
            assert stage.health(state, "draining").get("status") is None       # a drain stays a drain
            code, raw, headers = stage.refusal(state, {"prompt": "x"})
            body = json.loads(raw)
            assert code == 503 and body["error"]["code"] == "stage_down" and dict(headers) == {"Retry-After": "5"}
            t0 = time.monotonic()
            try:
                tick([6])
                raise AssertionError("a block on a dead link")
            except S.StageDown:
                assert time.monotonic() - t0 < 0.5           # no new block waits on a dead link
            r.cmd(cmd="heal")
            _wait(lambda: co.link is not None and co.link.state == "up" and co.epoch > epoch, 30, f"{fault}: the new link")
            assert co.length == 0 and stage.refusal(state, {"prompt": "x"}) is None
            assert "status" not in stage.health(state, "ok")
            tick([7, 8, 9])
            assert co.probe()["length"] == 3
    finally:
        r.stop()
    return (f"blackhole: stage_down after {took['blackhole']:.1f} s, cut: after {took['cut']:.2f} s; 503 with "
            f"Retry-After while down; a new epoch and empty stages after each heal")


def test_the_identity_names_the_stage_and_images_are_refused():
    d = _tmp()
    key = _keyfile(d)
    state = _prepared("--stage", "2:4", "--link-peer", "127.0.0.1:9100", "--link-key-file", key)
    fixed = {k: "x" for k in ("code", "family", "layers", "profile", "weights", "drafters", "rows", "tokenizer")}
    b = identity_mod.block({**state, "args": {}}, fixed, lambda: "served")
    assert b["stage"] == {"role": "tail", "lo": 2, "hi": 4}, b
    assert identity_mod.block({"args": {}}, fixed, lambda: "served")["stage"] is None
    # the handshake's block is what the route names: the same code digest, the same fields
    base = state["stage"]._base
    assert base["identity"] == identity_mod.VERSION and base["code"] == identity_mod.code_digest()
    assert base["family"] == "fake" and base["layers"] == 4 and len(base["weights"]) == 16
    ident = state["stage"].identity(stage.FakeStage(2, 4))
    assert ident["stage"] == {"role": "tail", "lo": 2, "hi": 4}
    assert L.mismatch(ident, {**base, "stage": {"role": "front", "lo": 0, "hi": 2}}) is None
    assert L.mismatch(ident, {**base, "stage": {"role": "front", "lo": 0, "hi": 3}}) == "stage"
    # images: 400 whatever the link's state; text passes; no stage, no refusal
    img = {"messages": [{"role": "user", "content": [{"type": "text", "text": "what is this"},
                                                    {"type": "image_url", "image_url": {"url": "data:x"}}]}]}
    code, raw, headers = stage.refusal(state, img)
    assert code == 400 and json.loads(raw)["error"]["code"] == "stage_text_only" and headers == ()
    text = {"messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]},
                         {"role": "assistant", "content": "ok"}]}
    assert stage.refusal(state, text) is None and stage.refusal({}, img) is None
    assert stage.health({}, "ok") == {}
    return "identity.stage is the range; an image part is a 400 stage_text_only"


# ------------------------------------------------------------------ two server processes (CPU torch)
LAUNCH = ("import sys, runpy; sys.modules['triton'] = None; sys.modules['triton.language'] = None;"
          "sys.argv = ['server/app.py'] + sys.argv[1:]; runpy.run_path('server/app.py', "
          "run_name='__main__')")


def _need_torch():
    if importlib.util.find_spec("torch") is None:
        from scriptmode import Skip
        raise Skip("the fake engine needs CPU torch")


def _server(tmp: str, name: str, *extra):
    port = _free_port()
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", tmp),
           "PYTHONPATH": ROOT, "CUDA_VISIBLE_DEVICES": ""}
    log = open(os.path.join(tmp, f"{name}.log"), "w")
    p = subprocess.Popen([sys.executable, "-u", "-c", LAUNCH, "--fake-engine", "--port", str(port),
                          "--served-model", "m", "--usage-ledger", "off", *extra],
                         cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
    p.logpath = log.name
    return port, p


def _log(p) -> str:
    with open(p.logpath) as f:
        return f.read()


def _up(port: int, p, seconds: float = 120) -> None:
    end = time.time() + seconds
    while time.time() < end:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            return
        except urllib.error.HTTPError:
            return
        except OSError:
            if p.poll() is not None:
                raise AssertionError(_log(p)[-3000:]) from None
            time.sleep(0.3)
    raise AssertionError("no /health in time:\n" + _log(p)[-3000:])


def _complete(port: int, prompt: str, n: int = 24):
    return _http(port, "/v1/completions", {"model": "m", "prompt": prompt, "max_tokens": n, "temperature": 0})


def test_two_server_processes_on_the_fake_engine():
    _need_torch()
    tmp = _tmp()
    key = _keyfile(tmp)
    link_port = _free_port()
    flags = ("--link-key-file", key)
    procs = []
    try:
        # a start that is refused exits before it loads anything, and says why
        for extra, why in ((("--stage", "0:2", "--link-listen", f"127.0.0.1:{link_port}"), "--link-key-file is required"),
                           (("--stage", "0:4", "--link-listen", f"127.0.0.1:{link_port}", *flags), "front 0:k or the tail"),
                           (("--link-peer", f"127.0.0.1:{link_port}"), "needs --stage"),
                           (("--stage", "2:4", "--link-peer", f"127.0.0.1:{link_port}", *flags, "--parallel", "2"),
                            "one slot")):
            _, p = _server(tmp, "refused", *extra)
            assert p.wait(120) != 0 and why in _log(p) and "REFUSING" in _log(p), _log(p)[-2000:]
        plain_port, plain = _server(tmp, "plain")
        procs.append(plain)
        fport, front = _server(tmp, "front", "--stage", "0:2", "--link-listen", f"127.0.0.1:{link_port}", *flags)
        procs.append(front)
        _up(fport, front)
        tport, tail = _server(tmp, "tail", "--stage", "2:4", "--link-peer", f"127.0.0.1:{link_port}", *flags)
        procs.append(tail)
        _up(tport, tail)
        _up(plain_port, plain)
        # the tail writes what the plain engine writes, and its blocks crossed the link
        code, _, want = _complete(plain_port, "Say something about stages.")
        code2, _, got = _complete(tport, "Say something about stages.")
        assert code == code2 == 200 and got["choices"][0]["text"] == want["choices"][0]["text"], (want, got)
        assert got["usage"]["completion_tokens"] == want["usage"]["completion_tokens"] > 0
        _, _, h = _http(tport, "/health")
        s = h["stage"]
        assert h["status"] == "ok" and (s["role"], s["lo"], s["hi"]) == ("tail", 2, 4), h
        assert s["link"]["state"] == "up" and s["link"]["epoch"] == 1 and s["link"]["bytes"]["rx"] > 0
        _, _, fh = _http(fport, "/health")
        assert fh["stage"]["role"] == "front" and fh["stage"]["steps"] > 0, fh
        assert "stage" not in _http(plain_port, "/health")[2]
        # identity: the tail names its range; the plain engine has none
        def ident(port):
            return _wait(lambda: (lambda r: r[2] if r[0] == 200 else None)(_http(port, "/v1/replica/identity")),
                         60, "the identity")
        assert ident(tport)["stage"] == {"role": "tail", "lo": 2, "hi": 4}
        assert ident(plain_port)["stage"] is None
        # the front serves no model route; images are refused by the tail
        code, _, body = _complete(fport, "hi")
        assert code == 409 and body["error"]["code"] == "stage_front", body
        code, _, body = _http(tport, "/v1/chat/completions", {"model": "m", "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]})
        assert code == 400 and body["error"]["code"] == "stage_text_only", body
        # a killed front: degraded, 503 with Retry-After; a new front: a new epoch, and it serves
        front.kill()
        front.wait(10)
        _wait(lambda: _http(tport, "/health")[2]["status"] == "degraded", 20, "degraded")
        code, headers, body = _complete(tport, "again")
        assert code == 503 and body["error"]["code"] == "stage_down" and headers.get("Retry-After") == "5", body
        fport, front = _server(tmp, "front2", "--stage", "0:2", "--link-listen", f"127.0.0.1:{link_port}", *flags)
        procs.append(front)
        _up(fport, front)
        _wait(lambda: _http(tport, "/health")[2]["stage"]["link"]["state"] == "up", 30, "the new link")
        h = _http(tport, "/health")[2]
        assert h["status"] == "ok" and h["stage"]["link"]["epoch"] == 2, h
        code, _, again = _complete(tport, "Say something about stages.")
        assert code == 200 and again["choices"][0]["text"] == want["choices"][0]["text"]
        logs = "".join(_log(p) for p in procs)
        assert KEY not in logs and "[stage] tail stage, layers [2, 4) of 4" in logs
        assert "link: transport tcp, bind 127.0.0.1:" in logs and "epoch 2" in logs
    finally:
        for p in procs:
            p.kill()
            p.wait(10)
    return "front and tail as two servers: the plain engine's text, 409 on the front, 503 while it is gone, epoch 2 after"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
