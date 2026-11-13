"""The engine API contract v1 against a running engine (SRV-748; 1.3: ENG-427, QA ENG-428):
`server/app.py --fake-engine` in a process of its own on a free port (tests/ports.py), with a
dashboard directory that holds no built page, a temporary home, ledger and admin token.

  1  /health carries engine_api (server/engine_api.py VERSION) for a loopback caller and
     validates against contracts/engine-api-v1/health.schema.json; a proxied caller without
     the token gets {status} alone; /v1/models and a chat completion answer 200; every GET route
     routes.json marks live answers 200, and one with a schema (1.3: dashboard/) validates
     against it; every family /metrics names is in metrics-families.json with its type;
     /dashboard/ is the not-built page, not a 5xx (it leaves the engine with ENG-413; not removed
     yet)
                                                                  test_the_engine_answers_the_contract
  2  the same from a copy of the tree without app/dashboard (the engine runs without the
     dashboard's sources), its dashboard directory left to the default
                                                                  test_the_engine_runs_without_app_dashboard

CPU torch (the fake engine's server imports it); without torch it prints `skipped: no torch`.
Run: python tests/test_engine_api_live.py
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
CONTRACT = os.path.join(ROOT, "contracts", "engine-api-v1")
ADMIN = "live-" + "0" * 40
MODEL = "tandem-live"
LAUNCH = ("import sys, runpy; sys.modules['triton'] = None; sys.modules['triton.language'] = None;"
          "sys.argv = ['server/app.py'] + sys.argv[1:]; runpy.run_path('server/app.py', "
          "run_name='__main__')")


class Engine:
    """server/app.py --fake-engine from `tree`, on a free port, stopped by close()."""

    def __init__(self, tree: str, dashboard_dir: str | None):
        from ports import free_port
        self.port = free_port()
        assert self.port not in (8000, 8009), self.port
        self.tmp = tempfile.mkdtemp(prefix="engine-api-live-")
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home,
               "PYTHONPATH": tree, "CUDA_VISIBLE_DEVICES": "", "QSE_ADMIN_TOKEN": ADMIN,
               "TMPDIR": self.tmp}
        extra = ["--dashboard-dir", dashboard_dir] if dashboard_dir is not None else []
        self.log = open(os.path.join(self.tmp, "engine.log"), "w")
        self.proc = subprocess.Popen(
            [sys.executable, "-c", LAUNCH, "--fake-engine", "--port", str(self.port),
             "--served-model", MODEL, "--fake-tps", "400",
             "--usage-ledger", os.path.join(self.tmp, "ledger.sqlite3"), *extra],
            cwd=tree, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        end = time.time() + 120
        while time.time() < end:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2)
                return
            except OSError:
                if self.proc.poll() is not None:
                    break
                time.sleep(0.3)
        self.close()
        raise AssertionError(f"the fake engine did not come up: {self.tail()}")

    def tail(self) -> str:
        with open(os.path.join(self.tmp, "engine.log")) as f:
            return f.read()[-3000:]

    def get(self, path: str, headers=None, token=ADMIN) -> tuple[int, bytes, str]:
        h = dict(headers or {})
        if token:
            h["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=h)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read(), r.headers.get("Content-Type") or ""
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers.get("Content-Type") or ""

    def chat(self) -> tuple[int, dict]:
        body = {"model": MODEL, "max_tokens": 16,
                "messages": [{"role": "user", "content": "Name three rivers."}]}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        self.log.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


def _check(e: Engine) -> str:
    from server import engine_api
    from tools import contract_check
    with open(os.path.join(CONTRACT, "routes.json")) as f:
        doc = json.load(f)
    # /health: the full answer for a loopback caller, {status} for a proxied one without the token
    st, raw, _ = e.get("/health", token=None)
    h = json.loads(raw)
    assert st == 200 and h["engine_api"] == engine_api.VERSION == doc["version"], h
    assert not contract_check.validate(h, contract_check.load("health", CONTRACT), root=CONTRACT)
    st, raw, _ = e.get("/health", headers={"X-Forwarded-For": "192.0.2.7"}, token=None)
    assert st == 200 and json.loads(raw) == {"status": "ok"}, raw
    # the data plane
    st, raw, _ = e.get("/v1/models")
    ids = [m["id"] for m in json.loads(raw)["data"]]
    assert st == 200 and MODEL in ids, (st, ids)
    st, body = e.chat()
    assert st == 200 and body["choices"][0]["message"], body
    # every live GET route
    st, raw, _ = e.get("/v1/setup/models")
    setup_id = json.loads(raw)["models"][0]["id"] if st == 200 else "none"
    bad, n, shaped = [], 0, 0
    for r in doc["routes"]:
        if not r["live"]:
            continue
        for p in [r["path"]] + [a for a in r["aliases"] if "{" not in a]:
            target = p.replace("{id}", MODEL if p.startswith("/v1/models/") else setup_id)
            if r.get("query"):
                target += "?" + r["query"]
            st, raw, _ = e.get(target)
            n += 1
            if st != 200:
                bad.append(f"{target}: {st} {raw[:200]!r}")
            elif r.get("schema"):
                # 1.3: the engine's reads against their shapes in this folder
                schema = contract_check.load(r["schema"], CONTRACT)
                root = os.path.dirname(os.path.join(CONTRACT, r["schema"]))
                errs = contract_check.validate(json.loads(raw), schema, root=root)
                shaped += 1
                bad += [f"{target}: {x}" for x in errs[:3]]
    assert not bad, bad
    # 1.3: every family the page names is in metrics-families.json with its type
    with open(os.path.join(CONTRACT, "metrics-families.json")) as f:
        fams = {x["name"]: x for x in json.load(f)["families"]}
    st, raw, _ = e.get("/metrics")
    typed = dict(ln.split()[2:4] for ln in raw.decode().splitlines() if ln.startswith("# TYPE "))
    assert st == 200 and typed, (st, raw[:200])
    wrong = {k: v for k, v in typed.items() if k not in fams or fams[k]["type"] != v}
    assert not wrong, f"families not in metrics-families.json or of another type: {wrong}"
    # the dashboard's files: none built here, so the placeholder page
    st, raw, ctype = e.get("/dashboard/", token=None)
    assert st == 200 and ctype.startswith("text/html") and b"not built" in raw, (st, raw[:200])
    out = f"engine_api {engine_api.VERSION}, {n} live GET routes 200 ({shaped} valid), " \
          f"{len(typed)} /metrics families listed, /dashboard/ not built"
    print(f"[engine-api] {out}", flush=True)
    return out


def test_the_engine_answers_the_contract():
    empty = tempfile.mkdtemp(prefix="engine-api-dash-")
    e = Engine(ROOT, os.path.join(empty, "dist"))           # a dashboard directory with nothing
    try:
        return _check(e)
    except AssertionError as exc:
        raise AssertionError(f"{exc}\n{e.tail()}") from None
    finally:
        e.close()
        shutil.rmtree(empty, ignore_errors=True)


def test_the_engine_runs_without_app_dashboard():
    tree = os.path.join(tempfile.mkdtemp(prefix="engine-api-tree-"), "src")
    skip = shutil.ignore_patterns(".git", "node_modules", "__pycache__", ".pytest_cache")
    dash = os.path.join(ROOT, "app", "dashboard")

    def ignore(d, names):
        out = set(skip(d, names))
        if os.path.realpath(d) == os.path.realpath(os.path.join(ROOT, "app")):
            out.add("dashboard")
        return out
    shutil.copytree(ROOT, tree, ignore=ignore, symlinks=True)
    assert os.path.isdir(dash) and not os.path.exists(os.path.join(tree, "app", "dashboard"))
    e = Engine(tree, None)
    try:
        return "without app/dashboard: " + _check(e)
    except AssertionError as exc:
        raise AssertionError(f"{exc}\n{e.tail()}") from None
    finally:
        e.close()
        shutil.rmtree(os.path.dirname(tree), ignore_errors=True)


if __name__ == "__main__":
    try:
        import torch  # noqa: F401
    except ImportError:
        print("skipped: no torch")
        sys.exit(0)
    from scriptmode import run
    run(globals())
