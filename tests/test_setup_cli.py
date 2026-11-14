"""`bin/tandem setup`, the Setup Guide in the terminal (ENG-435, QA ENG-436; server/setup/cli.py), on a
CPU against the engine's own Setup API: server/app.py's handlers on a free port in this process (the
serving engine's Handler, or setup mode's SetupHandler), in tests/test_setup_apply.py's Box (a
checkout in a temporary folder: never the box's own serve.env, run/ or weights).

"The page" below is the request TandemApp's Setup page sends for the same step (dashboard/src/setup/
api.ts: same origin, JSON), with the selection the page holds then (state.ts: the seed from
GET .../current, then the user's switches).

The test matrix (the Gherkin of ENG-435):

  1  the page's switches, ported from deps.ts and state.ts, on every model the registry has: a
     required tool never goes off; a tool switched off takes the tools that need it along, with
     the page's note; a tool switched on brings its `with_parent` tools; a tool whose dependency
     is off is refused with the page's reason; a profile with a shorter window takes the window
     down; a window snaps to the model's step; slots are held to the engine's limit
                                                            test_the_pages_switches
  2  parity, serving and setup mode alike: models, a model, the steps' seed (current), the box,
     the status, the community packs (GET and the sync, fetching off), the weights; per model the
     estimate and the plan of the seed, the FP8-cache switch on (each model that has one: the
     estimate's bytes a token drop), a community pack as the weights, apply --dry-run: the
     terminal sends the page's selection and prints the engine's answer unchanged (--json
     `result` equal to the page's body)                       test_same_answers_as_the_page
  3  the refusals are the engine's: a plan that does not fit (over_budget: rc 1, the page's plan,
     no apply sent), requests in flight (409 busy), a job that runs (409 job_running), a body the
     engine refuses (422 unknown_field from --selection): the status, code and body the page gets
                                                            test_the_refusals_are_the_engines
  4  a serving engine is never stopped unasked: apply without --restart is refused before any
     write (rc 4, no job); --restart without a terminal or --yes is refused; a no at the prompt
     is refused; --restart --yes starts the job (202); in setup mode a yes is enough and no
     --restart is needed                                     test_a_serving_engine_needs_restart_and_yes
  5  the job to its end through the engine's restart: setup mode, the real runner (stub scripts),
     the server goes away in the switch and a serving one comes back on the port; the terminal
     follows the stream again from its last event id and ends with the job the page reads (done,
     rc 0); the files written are the ones the page's install writes (run/setup.env, the job's
     folder), nothing else under the checkout         test_the_job_through_the_restart
  6  nothing answers: rc 3 and nothing started; an engine still loading (its pid alive) is not
     replaced; --start-setup starts setup mode with run/ops/start.sh --setup, answers, and stops
     it on the way out (no job), --keep-setup leaves it; against that real process (server/app.py
     --setup) one plan and one apply --dry-run are the page's answers and write nothing
                                                            test_nothing_answers
  7  Wren: ask loads it when it can, sends the page state and words only (tools false), and
     prints its answer; a Wren that cannot load is the engine's refusal       test_ask_wren
  8  bin/tandem setup (install.sh's control script) runs the module with this install's engine
     address, and its usage names it; --json and the exit codes for a wrong command, an unknown
     tool, and the guide without a terminal                  test_bin_tandem_setup_and_usage
  9  an admin password set: the terminal on the box is trusted-local and gets the same bodies as
     the page signed in; the page from the network without a token is 401
                                                            test_with_an_admin_password

Run: python tests/test_setup_cli.py
"""

from __future__ import annotations

import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ["QSE_COMMUNITY_URL"] = "off"     # the community sync fetches nothing here

from server import app  # noqa: E402
from server.api import auth  # noqa: E402
from server.setup import apply, cli, device as device_mod, registry  # noqa: E402

import test_setup_apply as SA  # noqa: E402
import test_chatd_lifecycle as C  # noqa: E402

ADMIN = "adm-" + "c1i7x" * 10
MODELS = list(registry.load_all()[0])


class FakeSampler:
    def sample(self):
        return {"name": "NVIDIA GB10", "temperature_c": 50.0, "power_w": 30.0, "sm_clock_mhz": 2400.0,
                "utilization": 0.05}


class FakeWren:
    """Wren's surface the routes call: status, touch, load, unload, chat (events)."""

    def __init__(self, state="not_loaded", can_load=True):
        self.state, self.can_load = state, can_load
        self.unloads, self.loads, self.bodies = [], 0, []

    def status(self):
        return {"name": "Wren", "model": "qwen3.5-2b", "fallback": False, "state": self.state,
                "reason": None if self.can_load else "not on disk", "load_s": None, "device_gib": None,
                "claim_gib": None, "can_load": self.can_load and self.state == "not_loaded",
                "borrowed": False, "window": 8192, "loading_s": None, "stats": {}}

    def touch(self):
        pass

    def load(self):
        self.loads += 1
        if self.can_load:
            self.state = "loaded"
        return self.status()

    def unload(self, why="asked"):
        self.unloads.append(why)
        self.state = "not_loaded"
        return self.status()

    def chat(self, body):
        self.bodies.append(body)
        yield {"type": "state", "state": "thinking"}
        yield {"type": "text", "delta": "Pick the "}
        yield {"type": "text", "delta": "recommended weights."}
        yield {"type": "done", "finish": "stop", "sources": [{"path": "docs/setup.md", "title": "Setup"}]}


class Server:
    """server/app.py's handler on 127.0.0.1 (a free port, or `port`), in this process; close()
    also cuts the open streams, as a restarting engine does."""

    def __init__(self, setup_mode: bool, port: int = 0, admin: str | None = None):
        handler = app.SetupHandler if setup_mode else app.Handler
        app.STATE.clear()
        app.STATE.update(auth=auth.Auth(admin, None), gpu_sampler=FakeSampler(), started=time.time() - 60,
                         version="0.15.4", setup_mode=setup_mode, dashboard_dir=None,
                         wren=Server.wren if hasattr(Server, "wren") else FakeWren())
        app.INFLIGHT.update(running=0, waiting=0, lane=0)
        device_mod.clear()          # its one-second cache would carry the last server's mode over
        conns = self.conns = []

        class S(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = True

            def process_request(self, request, client_address):
                conns.append(request)
                return super().process_request(request, client_address)

        self.httpd = S(("127.0.0.1", port), handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05},
                                       daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        for c in self.conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.httpd.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def page(srv, method: str, path: str, body=None, headers=None) -> tuple[int, object]:
    """The Setup page's request: same origin, JSON (api.ts get/post)."""
    h = {"Origin": srv.url, "Sec-Fetch-Site": "same-origin", "Accept": "*/*"}
    data = None
    if method == "POST":
        data = json.dumps(body if body is not None else {}).encode()
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(srv.url + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


class Tty(io.StringIO):
    def isatty(self):
        return True


def run_cli(url, *args, stdin=None, env=None):
    """The terminal's answer: (exit code, the --json object, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    old = {k: os.environ.get(k) for k in (env or {})}
    os.environ.update(env or {})
    try:
        rc = cli.main(["--json", "--url", url, "--poll", "0.1", *args], stdin=stdin or io.StringIO(""),
                      stdout=out, stderr=err)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    text = out.getvalue().strip()
    return rc, json.loads(text) if text else None, err.getvalue()


#: values of a moment, not of the answer: the uptime, the disk's free bytes as the tests write
MOMENT = ("uptime_s", "loading_s", "free_gb", "since", "checked_at", "left_s")


def norm(x):
    if isinstance(x, dict):
        return {k: norm(v) for k, v in x.items() if k not in MOMENT}
    if isinstance(x, list):
        return [norm(v) for v in x]
    return x


def same(cli_out: dict, page_body, what: str) -> None:
    assert norm(cli_out["result"]) == norm(page_body), (what, cli_out["result"], page_body)


def _seed(srv, mid):
    code, cur = page(srv, "GET", f"/v1/setup/models/{mid}/current")
    assert code == 200, cur
    return cur["selection"]


def _model(srv, mid):
    code, m = page(srv, "GET", f"/v1/setup/models/{mid}")
    assert code == 200, m
    return m


# ---------------------------------------------------------------------------------------- 1
def test_the_pages_switches():
    with SA.Box(), Server(setup_mode=False) as srv:
        seen_with_parent = seen_cascade = 0
        for mid in MODELS:
            m = _model(srv, mid)
            sel = cli.defaults(m)
            comps = sel["components"]
            for c in m["components"]:
                if c["required"]:
                    try:
                        cli.toggle(m, comps, c["id"], False)
                        raise AssertionError(f"{mid}: {c['id']} went off")
                    except cli.Usage as exc:
                        assert "always on" in str(exc)
                    continue
                deps_on = all(cli.is_on(cli._comp(m, d), comps) for d in c["depends_on"])
                if not deps_on and not cli.is_on(c, comps):
                    try:
                        cli.toggle(m, comps, c["id"], True)
                        raise AssertionError(f"{mid}: {c['id']} came on without its dependency")
                    except cli.Usage as exc:
                        assert "Needs " in str(exc), exc
                    continue
                on, note = cli.toggle(m, comps, c["id"], True)
                assert on[c["id"]] is True and note is None
                parents = [d["id"] for d in m["components"] if d.get("with_parent") and c["id"] in d["depends_on"]]
                for d in parents:
                    assert on[d] is True, (mid, c["id"], d)
                    seen_with_parent += 1
                kids = [d for d in cli.dependents_of(m, c["id"]) if cli.is_on(d, on) and not d["required"]]
                off, note = cli.toggle(m, on, c["id"], False)
                assert off[c["id"]] is False and all(off[d["id"]] is False for d in kids), (mid, c["id"])
                if kids:
                    seen_cascade += 1
                    assert note.endswith("off too.") and cli.lower_label(c["label"]) in note, note
                # the engine takes what the page's switches make
                code, e = page(srv, "POST", "/v1/setup/estimate", dict(sel, components=off))
                assert code == 200, (mid, c["id"], e)
            # the window: a step's multiple, inside the range; a shorter profile window takes it down
            r = m["parallelism"]["window"]
            got, notes = cli.set_parallel(m, sel, "window", r["min"] + r["step"] * 1.4)
            assert got["window"] % r["step"] == 0 and notes and "nearest length" in notes[0]
            got, _ = cli.set_parallel(m, sel, "window", 10 ** 9)
            assert got["window"] == cli.window_max(m, sel["profile"])
            got, _ = cli.set_parallel(m, sel, "slots", 10 ** 6)
            assert got["slots"] == cli.slots_max(m)
            for p in m["profiles"]:
                if p.get("max_window") and p["max_window"] < r["max"]:
                    big = dict(sel, window=r["max"])
                    nxt, notes = cli.set_profile(m, big, p["id"])
                    assert nxt["window"] == p["max_window"] and notes, (mid, p["id"])
        assert seen_with_parent and seen_cascade, (seen_with_parent, seen_cascade)
        # the page's own named cases (dashboard/tests/setup-state.test.ts) on Flash-Next
        fn = _model(srv, "qwen3.8-flash-next")
        d = cli.defaults(fn)["components"]
        on, _ = cli.toggle(fn, d, "conductor", True)
        assert on["conductor"] and on["laya"] and not on["narrator"], on
        try:
            cli.toggle(fn, dict(d, disk_tier=False, nvme_spill=False), "nvme_spill", True)
            raise AssertionError("nvme_spill without the disk cache")
        except cli.Usage as exc:
            assert str(exc).endswith("Needs the disk cache"), exc
        assert [c["id"] for c in cli.dependents_of(fn, "disk_tier")] == ["nvme_spill"]


# ---------------------------------------------------------------------------------------- 2
def _parity(srv, mode: str) -> list[str]:
    done = []
    reads = [("models", "/v1/setup/models"), ("device", "/v1/setup/device"),
             ("status", "/v1/setup/status"), ("community", "/v1/setup/community"),
             ("weights", "/v1/setup/weights")]
    for cmd, path in reads:
        code, body = page(srv, "GET", path)
        rc, out, err = run_cli(srv.url, cmd)
        assert code == 200 and rc == 0 and out["ok"] and out["mode"] == mode, (cmd, rc, out, err)
        same(out, body, cmd)
        done.append(cmd)
    code, body = page(srv, "POST", "/v1/setup/community/sync", {})
    rc, out, _ = run_cli(srv.url, "community", "--sync")
    assert code == 200 and rc == 0
    same(out, body, "community --sync")
    fp8 = 0
    for mid in MODELS:
        for cmd, path in (("show", f"/v1/setup/models/{mid}"), ("current", f"/v1/setup/models/{mid}/current")):
            code, body = page(srv, "GET", path)
            rc, out, _ = run_cli(srv.url, cmd, mid)
            assert code == 200 and rc == 0, (cmd, mid, out)
            same(out, body, f"{cmd} {mid}")
        m = _model(srv, mid)
        seed = _seed(srv, mid)
        # the estimate and the plan of the box's own setup (the page's first view of the model)
        code, est = page(srv, "POST", "/v1/setup/estimate", seed)
        rc, out, _ = run_cli(srv.url, "estimate", mid)
        assert code == 200 and out["selection"] == seed, (mid, out["selection"], seed)
        assert rc == (0 if est["fits"] else 1), (mid, rc)
        same(out, est, f"estimate {mid}")
        code, plan = page(srv, "POST", "/v1/setup/plan", {"selection": seed})
        rc, out, _ = run_cli(srv.url, "plan", mid)
        assert code == 200 and out["selection"] == seed and rc == (0 if plan["ok"] else 1), (mid, rc)
        same(out, plan, f"plan {mid}")
        code, dry = page(srv, "POST", "/v1/setup/apply", {"selection": seed, "dry_run": True})
        rc, out, _ = run_cli(srv.url, "apply", mid, "--dry-run")
        assert code == 200 and dry["dry_run"] is True and out["selection"] == seed
        same(out, dry, f"apply --dry-run {mid}")
        # the FP8-cache switch (SPD-150): the page's toggle of the tool, the estimate with it
        if cli._comp(m, "kv_fp8") is not None:
            want = dict(seed, components=dict(seed["components"], kv_fp8=True))
            code, on = page(srv, "POST", "/v1/setup/estimate", want)
            rc, out, _ = run_cli(srv.url, "estimate", mid, "--on", "kv_fp8")
            assert code == 200 and out["selection"] == want, (mid, out["selection"])
            same(out, on, f"estimate {mid} --on kv_fp8")
            if not seed["components"].get("kv_fp8"):
                assert on["kv_bytes_per_token"] < est["kv_bytes_per_token"], (mid, on, est)
            code, plan = page(srv, "POST", "/v1/setup/plan", {"selection": want})
            rc, out, _ = run_cli(srv.url, "plan", mid, "--on", "kv_fp8")
            same(out, plan, f"plan {mid} --on kv_fp8")
            fp8 += 1
        # a community pack as the weights (the page's weights step: the pack's card)
        for c in m.get("community") or []:
            if not c["profile"].startswith("community/"):
                continue
            want = _page_profile(m, seed, c)
            code, est_c = page(srv, "POST", "/v1/setup/estimate", want)
            rc, out, _ = run_cli(srv.url, "estimate", mid, "--profile", c["profile"])
            assert out["selection"] == want, (mid, out["selection"], want)
            same(out, est_c, f"estimate {mid} {c['profile']}")
            code, plan = page(srv, "POST", "/v1/setup/plan", {"selection": want})
            rc, out, _ = run_cli(srv.url, "plan", mid, "--profile", c["profile"])
            same(out, plan, f"plan {mid} {c['profile']}")
            done.append(c["profile"])
    assert fp8 >= 5, fp8
    return done


def _page_profile(m, seed, e):
    """state.ts's `profile` action for a pack, written out here: the pack's profile is its `like`
    profile with the pack over it (community.ts profileOfEntry), so the window goes down to that
    profile's largest; a pack without images turns the Vision tool off."""
    own = m["profiles"]
    like = (next((p for p in own if p["id"] == e["memory"]["like"]), None)
            or next((p for p in own if p["format"] == e["format"]), None) or own[0])
    want = dict(seed, profile=e["profile"], components=dict(seed["components"]))
    top = min(m["parallelism"]["window"]["max"], like["max_window"]) if like.get("max_window") else \
        m["parallelism"]["window"]["max"]
    if want["window"] > top:
        want["window"] = top
    if not e["modalities"]["image"] and any(c["id"] == "vision" for c in m["components"]) and \
            want["components"].get("vision") is not False:
        want["components"]["vision"] = False
    return want


def test_same_answers_as_the_page():
    with SA.Box():
        with Server(setup_mode=False) as srv:
            a = _parity(srv, "serving")
        with Server(setup_mode=True) as srv:
            b = _parity(srv, "setup")
    assert any(x.startswith("community/") for x in a) and a == b, (a, b)
    return f"{len(MODELS)} models, reads and {len(a)} named checks in both modes"


# ---------------------------------------------------------------------------------------- 3
def test_the_refusals_are_the_engines():
    with SA.Box() as box:
        spawned = []
        real = apply.spawn
        apply.spawn = lambda jid: spawned.append(jid) or 4242
        try:
            with Server(setup_mode=False) as srv:
                mid = SA.M["id"]
                seed = _seed(srv, mid)
                # a plan that does not fit (the pool off: every window at load)
                bad = dict(seed, kv_pool=False)
                code, plan = page(srv, "POST", "/v1/setup/plan", {"selection": bad})
                assert code == 200 and not plan["ok"] and "over_budget" in plan["error_codes"], plan
                rc, out, _ = run_cli(srv.url, "plan", mid, "--kv-pool", "off")
                assert rc == 1 and out["error"]["code"] == "over_budget" and out["selection"] == bad
                same(out, plan, "plan over_budget")
                code, refused = page(srv, "POST", "/v1/setup/apply", {"selection": bad})
                assert code == 409 and refused["error"]["code"] == "over_budget"
                rc, out, _ = run_cli(srv.url, "apply", mid, "--kv-pool", "off", "--restart", "--yes")
                assert rc == 1 and out["error"]["code"] == "over_budget" and not spawned
                # the page's 409 carries the plan apply computed; the terminal's is POST /plan's (dry_run false)
                assert norm(dict(out["result"], dry_run=None)) == norm(dict(refused["plan"], dry_run=None))
                # requests in flight
                app.INFLIGHT["running"] = 1
                code, busy = page(srv, "POST", "/v1/setup/apply", {"selection": seed})
                rc, out, _ = run_cli(srv.url, "apply", mid, "--restart", "--yes", "--no-follow")
                assert code == 409 and rc == 1 and out["error"]["status"] == 409, out
                assert out["error"]["code"] == "busy" and out["result"] == busy and not spawned
                app.INFLIGHT["running"] = 0
                # a job that runs
                rc, out, _ = run_cli(srv.url, "apply", mid, "--restart", "--yes", "--no-follow")
                assert rc == 0 and out["result"]["state"] == "queued" and spawned == [out["result"]["id"]]
                code, running = page(srv, "POST", "/v1/setup/apply", {"selection": seed})
                rc, out, _ = run_cli(srv.url, "apply", mid, "--restart", "--yes", "--no-follow")
                assert code == 409 and rc == 1 and out["error"]["code"] == "job_running"
                assert out["result"] == running and len(spawned) == 1
                # a body the engine refuses: a script's selection file with a command in it
                f = os.path.join(box.tmp, "sel.json")
                with open(f, "w") as fh:
                    json.dump({"model": mid, "cmd": "id"}, fh)
                code, body = page(srv, "POST", "/v1/setup/plan", {"selection": {"model": mid, "cmd": "id"}})
                rc, out, _ = run_cli(srv.url, "plan", mid, "--selection", f)
                assert code == 422 and rc == 1 and out["error"]["status"] == 422
                assert out["error"]["code"] == "unknown_field" and out["result"] == body
                # an unknown model is the engine's 404 too
                code, body = page(srv, "GET", "/v1/setup/models/nope")
                rc, out, _ = run_cli(srv.url, "show", "nope")
                assert code == 404 and rc == 1 and out["result"] == body
        finally:
            apply.spawn = real


# ---------------------------------------------------------------------------------------- 4
def _jobs():
    d = apply.jobs_dir()
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def test_a_serving_engine_needs_restart_and_yes():
    with SA.Box():
        spawned = []
        real = apply.spawn
        apply.spawn = lambda jid: spawned.append(jid) or 4242
        try:
            mid = SA.M["id"]
            with Server(setup_mode=False) as srv:
                rc, out, _ = run_cli(srv.url, "apply", mid, "--yes")
                assert rc == 4 and "--restart" in out["error"]["message"] and not _jobs() and not spawned
                rc, out, _ = run_cli(srv.url, "apply", mid, "--restart")
                assert rc == 4 and "--yes" in out["error"]["message"] and not _jobs()
                # at a terminal: a no changes nothing (not --json: the prompt is the terminal's)
                err = io.StringIO()
                rc = cli.main(["--url", srv.url, "apply", mid, "--restart"], stdin=Tty("n\n"),
                              stdout=io.StringIO(), stderr=err)
                assert rc == 4 and "Go on? [y/N]" in err.getvalue() and not _jobs(), err.getvalue()
                rc = cli.main(["--url", srv.url, "apply", mid, "--restart", "--no-follow"], stdin=Tty("y\n"),
                              stdout=io.StringIO(), stderr=io.StringIO())
                assert rc == 0 and len(spawned) == 1 and len(_jobs()) == 1
                # retry follows the same rule
                jid = spawned[0]
                rc, out, _ = run_cli(srv.url, "retry", jid, "--yes")
                assert rc == 4 and "--restart" in out["error"]["message"]
            shutil.rmtree(apply.jobs_dir())
            spawned.clear()
            with Server(setup_mode=True) as srv:
                rc, out, _ = run_cli(srv.url, "apply", mid)
                assert rc == 4 and "--yes" in out["error"]["message"] and not _jobs(), (rc, out)
                rc, out, _ = run_cli(srv.url, "apply", mid, "--yes", "--no-follow")
                assert rc == 0 and out["mode"] == "setup" and spawned == [out["result"]["id"]]
        finally:
            apply.spawn = real


# ---------------------------------------------------------------------------------------- 5
def test_the_job_through_the_restart():
    with SA.Box() as box:
        mid = SA.M["id"]
        holder = {}
        before = box.files()

        class Restarting(SA.Recorder):
            def __call__(self, argv, *, timeout, env, cwd, line):
                rc = super().__call__(argv, timeout=timeout, env=env, cwd=cwd, line=line)
                key = " ".join(os.path.basename(a) for a in argv[1:])
                if key.startswith("use-profile.sh") and "--fetch-only" not in key:
                    # the switch: setup mode goes, the engine comes back on the same port
                    port = holder["srv"].port
                    holder["srv"].close()
                    time.sleep(0.5)
                    holder["srv"] = Server(setup_mode=False, port=port)
                return rc

        real = apply.spawn

        def spawn(jid):
            spec = apply.job_spec(jid)
            r = apply.Runner(jid, cmd=Restarting(), http=SA._smoke_ok(spec["selection"]["profile"]),
                             ops=box.ops, sleep=lambda s: None)
            threading.Thread(target=r.main, daemon=True).start()
            return 4242

        apply.spawn = spawn
        try:
            holder["srv"] = Server(setup_mode=True)
            url = holder["srv"].url
            seed = _seed(holder["srv"], mid)
            rc, out, err = run_cli(url, "apply", mid, "--yes")
            assert rc == 0, (out, err)
            job = out["result"]
            assert job["state"] == "done" and out["mode"] == "setup", job
            assert [s["state"] for s in job["steps"] if s["id"] == "switch"] == ["done"]
            assert "does not answer" in err and "answers again" in err, err
            code, body = page(holder["srv"], "GET", f"/v1/setup/jobs/{job['id']}?format=json")
            assert code == 200 and body == job
            code, d = page(holder["srv"], "GET", "/v1/setup/device")
            assert d["mode"] == "serving"
            # what the install wrote: run/setup.env and the job's folder, as the page's install
            new = sorted(box.files() - before)
            assert any(p.endswith("run/setup.env") for p in new), new
            other = [p for p in new if not (p.endswith("run/setup.env") or "/run/setup-jobs/" in p)]
            assert not other, other
            assert seed["model"] == mid
        finally:
            apply.spawn = real
            holder["srv"].close()


# ---------------------------------------------------------------------------------------- 6
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _install(port: int) -> tuple[str, str]:
    """A TANDEM_DIR whose run/ops/start.sh --setup starts the real setup-mode server
    (server/app.py --setup) on `port` and whose stop.sh stops it; each writes a marker."""
    d = tempfile.mkdtemp(prefix="setup-cli-")
    marks = os.path.join(d, "markers")
    logs = os.path.join(d, "src", "logs")
    os.makedirs(logs)
    os.makedirs(os.path.join(d, "home"))
    launch = ("import sys, runpy; sys.modules['triton'] = None; sys.modules['triton.language'] = None;"
              "sys.argv = ['server/app.py'] + sys.argv[1:]; runpy.run_path('server/app.py', run_name='__main__')")
    C._write(os.path.join(d, "run", "ops", "start.sh"), f"""#!/bin/bash
echo "start.sh $*" >> {marks}
cd {ROOT}
HOME={d}/home PYTHONPATH={ROOT} CUDA_VISIBLE_DEVICES= QSE_SETUP_MODE=1 setsid nohup {sys.executable} -c "{launch}" \\
    --setup --host 127.0.0.1 --port {port} > {logs}/engine.log 2>&1 < /dev/null &
echo $! > {logs}/engine.pid
for i in $(seq 1 240); do
    {sys.executable} -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:{port}/health', timeout=2)" 2>/dev/null && exit 0
    sleep 0.5
done
exit 1
""")
    C._write(os.path.join(d, "run", "ops", "stop.sh"), f"""#!/bin/bash
echo "stop.sh $*" >> {marks}
pid=$(cat {logs}/engine.pid 2>/dev/null) && kill "$pid" 2>/dev/null
for i in $(seq 1 40); do kill -0 "$pid" 2>/dev/null || exit 0; sleep 0.25; done
kill -9 "$pid" 2>/dev/null; exit 0
""")
    return d, marks


def _marks(path):
    return open(path).read().splitlines() if os.path.exists(path) else []


def test_nothing_answers():
    port = _free_port()
    d, marks = _install(port)
    url = f"http://127.0.0.1:{port}"
    pid = None
    try:
        rc, out, _ = run_cli(url, "models", "--tandem-dir", d)
        assert rc == 3 and "--start-setup" in out["error"]["message"] and not _marks(marks), out
        # an engine that is loading (its pid alive) is never replaced
        with open(os.path.join(d, "src", "logs", "engine.pid"), "w") as fh:
            fh.write(str(os.getpid()))
        rc, out, _ = run_cli(url, "models", "--tandem-dir", d, "--start-setup")
        assert rc == 3 and "starting or loading" in out["error"]["message"] and not _marks(marks)
        os.remove(os.path.join(d, "src", "logs", "engine.pid"))
        # at a terminal, a no starts nothing
        rc = cli.main(["--url", url, "--tandem-dir", d, "models"], stdin=Tty("n\n"),
                      stdout=io.StringIO(), stderr=io.StringIO())
        assert rc == 4 and not _marks(marks)
        rc, out, err = run_cli(url, "models", "--tandem-dir", d, "--start-setup")
        assert rc == 0 and out["mode"] == "setup" and out["result"]["models"], (out, err)
        assert _marks(marks) == ["start.sh --setup", "stop.sh 60"], _marks(marks)
        pid = int(open(os.path.join(d, "src", "logs", "engine.pid")).read())
        try:
            os.kill(pid, 0)
            raise AssertionError("setup mode still runs")
        except ProcessLookupError:
            pass
        rc, out, _ = run_cli(url, "device", "--tandem-dir", d, "--start-setup", "--keep-setup")
        assert rc == 0 and out["result"]["mode"] == "setup"
        assert _marks(marks)[2:] == ["start.sh --setup"], _marks(marks)
        pid = int(open(os.path.join(d, "src", "logs", "engine.pid")).read())
        os.kill(pid, 0)
        # one plan and one dry apply against that real setup-mode process: the page's answers, and
        # nothing written (no job folder, no run/setup.env)
        class Real:
            pass
        real = Real()
        real.url = url
        mid = MODELS[0]
        code, cur = page(real, "GET", f"/v1/setup/models/{mid}/current")
        code, plan = page(real, "POST", "/v1/setup/plan", {"selection": cur["selection"]})
        rc, out, _ = run_cli(url, "plan", mid)
        assert code == 200 and rc == (0 if plan["ok"] else 1) and out["mode"] == "setup", out
        same(out, plan, "plan against server/app.py --setup")
        code, dry = page(real, "POST", "/v1/setup/apply", {"selection": cur["selection"], "dry_run": True})
        rc, out, _ = run_cli(url, "apply", mid, "--dry-run")
        assert code == 200 and dry["dry_run"] is True
        same(out, dry, "apply --dry-run against server/app.py --setup")
        assert not os.path.exists(os.path.join(ROOT, "run", "setup-jobs")) or \
            not os.listdir(os.path.join(ROOT, "run", "setup-jobs"))
        assert not os.path.exists(os.path.join(ROOT, "run", "setup.env"))
    finally:
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------------------- 7
def test_ask_wren():
    with SA.Box():
        w = Server.wren = FakeWren()
        try:
            with Server(setup_mode=True) as srv:
                mid = SA.M["id"]
                rc, out, err = run_cli(srv.url, "ask", "Which", "weights?", "--model", mid)
                assert rc == 0 and out["result"]["answer"] == "Pick the recommended weights.", (out, err)
                assert w.loads == 1 and out["result"]["sources"][0]["path"] == "docs/setup.md"
                body = w.bodies[-1]
                assert body["tools"] is False and body["messages"] == [{"role": "user", "content": "Which weights?"}]
                assert body["page"]["model"] == mid and body["page"]["selection"] == _seed(srv, mid)
                assert body["page"]["step"] == "review"
                # the text form prints the answer
                text = io.StringIO()
                assert cli.main(["--url", srv.url, "ask", "hi"], stdout=text, stderr=io.StringIO()) == 0
                assert "Pick the recommended weights." in text.getvalue()
                assert w.bodies[-1]["page"] == {"model": None, "step": "models", "step_index": 0, "selection": {}}
            Server.wren = FakeWren(can_load=False)
            with Server(setup_mode=True) as srv:
                rc, out, _ = run_cli(srv.url, "ask", "hi")
                assert rc == 1 and out["error"]["code"] == "wren_not_loaded", out
        finally:
            del Server.wren


# ---------------------------------------------------------------------------------------- 8
def test_bin_tandem_setup_and_usage():
    with SA.Box(), Server(setup_mode=True) as srv:
        d = tempfile.mkdtemp(prefix="setup-ctl-")
        try:
            C._write(os.path.join(d, "run", "ops", "serve.env"),
                     f"REPO={ROOT}\nPY={sys.executable}\nPORT={srv.port}\n", 0o644)
            C._write(os.path.join(d, "bin", "tandem"), C.control_script(d))
            env = dict(os.environ, PATH="/usr/bin:/bin")
            r = subprocess.run([os.path.join(d, "bin", "tandem"), "setup", "models", "--json"],
                               capture_output=True, text=True, env=env, timeout=120, stdin=subprocess.DEVNULL)
            assert r.returncode == 0, r.stdout + r.stderr
            out = json.loads(r.stdout)
            code, body = page(srv, "GET", "/v1/setup/models")
            assert out["url"] == srv.url and out["mode"] == "setup" and out["result"] == body
            r = subprocess.run([os.path.join(d, "bin", "tandem"), "setup", "--help"], capture_output=True,
                               text=True, env=env, timeout=60)
            assert r.returncode == 0 and "bin/tandem setup" in r.stdout and "--restart" in r.stdout
            r = subprocess.run([os.path.join(d, "bin", "tandem"), "nonsense"], capture_output=True,
                               text=True, env=env, timeout=60)
            assert r.returncode == 2 and "| setup [command] |" in r.stderr, r.stderr
        finally:
            shutil.rmtree(d, ignore_errors=True)
        # --json and the exit codes
        rc, out, _ = run_cli(srv.url, "frobnicate")
        assert rc == 2
        rc, out, _ = run_cli(srv.url, "plan", SA.M["id"], "--on", "no_such_tool")
        assert rc == 2 and out["error"]["code"] == "usage" and "no tool called no_such_tool" in out["error"]["message"]
        rc, out, _ = run_cli(srv.url, "plan")
        assert rc == 2 and "needs a MODEL" in out["error"]["message"]
        rc, out, _ = run_cli(srv.url, "models", "--profile", "x")
        assert rc == 2 and "takes no choices" in out["error"]["message"]
        rc, out, _ = run_cli(srv.url)                           # the guide, with no terminal
        assert rc == 2 and "terminal" in out["error"]["message"]
        rc, out, _ = run_cli(srv.url, "plan", SA.M["id"], "--set", "no_such_field=1")
        assert rc == 2 and "no setting" in out["error"]["message"]


# ---------------------------------------------------------------------------------------- 9
def test_with_an_admin_password():
    with SA.Box(), Server(setup_mode=False, admin=ADMIN) as srv:
        lan = {"X-Forwarded-For": "203.0.113.9", "X-Real-IP": "203.0.113.9"}
        code, _ = page(srv, "GET", "/v1/setup/models", headers=lan)
        assert code == 401
        mid = SA.M["id"]
        code, seed = page(srv, "GET", f"/v1/setup/models/{mid}/current",
                          headers=dict(lan, Authorization=f"Bearer {ADMIN}"))
        assert code == 200
        code, plan = page(srv, "POST", "/v1/setup/plan", {"selection": seed["selection"]},
                          headers=dict(lan, Authorization=f"Bearer {ADMIN}"))
        rc, out, err = run_cli(srv.url, "plan", mid)
        assert code == 200 and rc in (0, 1) and out["selection"] == seed["selection"], (out, err)
        same(out, plan, "plan with a password")
        assert ADMIN not in json.dumps(out) and ADMIN not in err


def test_the_guide_at_a_terminal():
    """The guide in a terminal: Enter at every step keeps the box's setup; the review is the
    engine's plan and prints the same command for a script; on a serving engine without
    --restart nothing starts; in setup mode a yes starts the job."""
    with SA.Box():
        spawned = []
        real = apply.spawn
        apply.spawn = lambda jid: spawned.append(jid) or 4242
        try:
            mid = SA.M["id"]
            # the model, then Enter at the weights, the tools, slots, window, floor, pool, settings
            answers = "\n".join([mid] + [""] * 7) + "\n"
            with Server(setup_mode=False) as srv:
                out, err = io.StringIO(), io.StringIO()
                rc = cli.main(["--url", srv.url], stdin=Tty(answers), stdout=out, stderr=err)
                text = out.getvalue()
                assert rc == 0, (text, err.getvalue())
                assert "6. Review" in text and f"bin/tandem setup apply {mid}" in text
                assert "Nothing changed" in text and not spawned
            with Server(setup_mode=True) as srv:
                out, err = io.StringIO(), io.StringIO()
                rc = cli.main(["--url", srv.url, "--no-follow"], stdin=Tty(answers + "y\n"), stdout=out,
                              stderr=err)
                assert rc == 0 and len(spawned) == 1, (out.getvalue(), err.getvalue())
        finally:
            apply.spawn = real


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                note = fn()
                print(f"ok   {name}" + (f": {note}" if note else ""))
            except Exception as exc:                              # noqa: BLE001
                failed += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failed else 0)
