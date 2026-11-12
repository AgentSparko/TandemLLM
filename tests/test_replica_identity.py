"""`GET /v1/replica/identity` (SRV-741, server/identity.py): the block two replicas compare.

The digests, the code digest's file set, the 503 until the fixed part is ready, and which field a
setting moves, on the module with a stub state; then the route on fake engines in processes of
their own (CPU torch; skipped without it): equal flags give equal blocks, `--max-len` moves only
`max_len`, a decode setting only `decode`, a `FAKE_IDENTITY_*` override only its field, and the
route answers whoever /v1/models answers.

Run: python tests/test_replica_identity.py
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import identity  # noqa: E402

ADMIN = "e2e-" + "0" * 40
LAUNCH = ("import sys, runpy; sys.modules['triton'] = None; sys.modules['triton.language'] = None;"
          "sys.argv = ['server/app.py'] + sys.argv[1:]; runpy.run_path('server/app.py', "
          "run_name='__main__')")
FIELDS = ("identity", "served_model", "base_model", "version", "code", "family", "layers",
          "profile", "weights", "drafters", "rows", "tokenizer", "decode", "max_len", "stage")
DECODE = ("tree", "sampled_tree", "reasoning_format", "thinking_default", "temperature", "top_p",
          "top_k", "parallel", "kv_fp8")


# --------------------------------------------------------------------------- the module
class _Tok:
    def __call__(self, text, add_special_tokens=False):
        return types.SimpleNamespace(input_ids=[ord(c) for c in text])

    def __len__(self):
        return 1000


def _state(**kw) -> dict:
    eng = types.SimpleNamespace(cfg=types.SimpleNamespace(num_hidden_layers=48))
    s = {"args": {"model": None, "parallel": 2}, "tok": _Tok(), "engine": eng, "model": "m",
         "version": "1.0", "tree": True, "sampled_tree": "det", "reasoning_format": "tags",
         "thinking_default": "on", "temperature": 0.0, "top_p": 1.0, "top_k": 0, "max_len": 8192}
    s.update(kw)
    return s


def _profile():
    return {"name": "p1", "format": "exl3", "weights": None, "kv_cache": "e4m3"}


def _ready(state, root=identity.ROOT) -> dict:
    code, body, _ = identity.answer(state, lambda: "served", _profile, root)
    end = time.time() + 60
    while code == 503 and time.time() < end:
        time.sleep(0.05)
        code, body, _ = identity.answer(state, lambda: "served", _profile, root)
    assert code == 200, (code, body)
    return body


def _differ(a: dict, b: dict) -> set:
    return {k for k in set(a) | set(b) if a.get(k) != b.get(k)}


def test_digest_is_canonical_and_short():
    a = identity.digest({"a": 1, "b": [1, 2, {"c": None}]})
    assert a == identity.digest({"b": [1, 2, {"c": None}], "a": 1})
    assert len(a) == 16 and int(a, 16) >= 0
    assert a != identity.digest({"a": 1, "b": [2, 1, {"c": None}]})


def _tree(root: str, files: dict) -> None:
    for rel, text in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)


def test_code_digest_counts_source_only(tmp_path):
    root = str(tmp_path)
    _tree(root, {"engine/a.py": "x = 1\n", "engine/k/k.cu": "//\n", "server/s.py": "y\n",
                 "tools/t.json": "{}\n", "ops/serve.env": "A=1\n"})
    d0 = identity.code_digest(root)
    # not code: ops/, caches, build outputs, notes, other kinds of files
    _tree(root, {"ops/serve.env": "A=2\n", "ops/new.py": "z\n", "engine/__pycache__/a.cpython.pyc": "b",
                 "engine/__pycache__/a.py": "cached\n", "engine/build/k.cu": "built\n",
                 "engine/notes.md": "text\n", "engine/run.log": "1\n", "engine/.hidden/h.py": "h\n",
                 "README.py": "outside the folders\n"})
    assert identity.code_digest(root) == d0
    # code: a changed file, a new kernel header, a moved file
    _tree(root, {"engine/a.py": "x = 2\n"})
    d1 = identity.code_digest(root)
    assert d1 != d0
    _tree(root, {"engine/k/k.cuh": "//\n"})
    d2 = identity.code_digest(root)
    assert d2 not in (d0, d1)
    os.rename(os.path.join(root, "server/s.py"), os.path.join(root, "server/s2.py"))
    assert identity.code_digest(root) not in (d0, d1, d2)


def test_code_digest_is_stable_across_processes():
    here = identity.code_digest(identity.ROOT)
    prog = "import sys; sys.path.insert(0, sys.argv[1]); from server import identity; print(identity.code_digest())"
    got = {subprocess.run([sys.executable, "-c", prog, ROOT], capture_output=True, text=True,
                          check=True, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                           "PYTHONHASHSEED": str(seed)}).stdout.strip()
           for seed in (1, 2)}
    assert got == {here}, (got, here)


def test_identity_module_imports_no_proxy_and_no_app():
    tree = ast.parse(open(os.path.join(ROOT, "server", "identity.py")).read())
    names = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            names += [n.module or ""] + [f"{n.module}.{a.name}" for a in n.names]
    bad = [x for x in names if x.split(".")[0] in ("proxy", "app")
           or x.startswith(("server.app", "server.proxy", "app."))]
    assert not bad, bad
    assert "server.diskcache" in names       # the rows' fingerprint, as the disk tier keys them


def test_pending_until_the_fixed_part_is_ready():
    gate = threading.Event()
    calls = []

    def slow():
        calls.append(1)
        gate.wait(10)
        return _profile()

    st = _state()
    for _ in range(3):
        code, body, headers = identity.answer(st, lambda: "served", slow)
        assert code == 503 and body["error"]["type"] == "identity_pending", body
        assert body["error"]["code"] == "identity_pending" and ("Retry-After", "1") in headers
    gate.set()
    st["identity_thread"].join(10)
    assert len(calls) == 1                   # one thread, whatever the number of calls
    assert st["identity_thread"].name == "replica-identity"
    code, body, headers = identity.answer(st, lambda: "served", slow)
    assert code == 200 and headers == () and len(calls) == 1
    assert tuple(body) == FIELDS and tuple(body["decode"]) == DECODE, body


def test_the_block_fields_and_their_sources():
    from engine.models import default_arch
    b = _ready(_state())
    assert b["identity"] == 1 and b["served_model"] == "served" and b["base_model"] == "m"
    assert b["version"] == "1.0" and b["stage"] is None and b["max_len"] == 8192
    assert b["family"] == default_arch().name           # no checkpoint folder: the default family
    assert b["layers"] == 48 and b["profile"] == "p1"
    assert b["decode"] == {"tree": True, "sampled_tree": "det", "reasoning_format": "tags",
                           "thinking_default": "on", "temperature": 0.0, "top_p": 1.0, "top_k": 0,
                           "parallel": 2, "kv_fp8": True}
    for k in ("code", "weights", "drafters", "rows", "tokenizer"):
        assert isinstance(b[k], str) and len(b[k]) == 16, (k, b[k])
    assert b["tokenizer"] == identity.digest([1000, [ord(c) for c in identity.PROBE]])


def test_a_checkpoint_folder_names_its_family_and_layers(tmp_path):
    from engine.models import REGISTRY
    arch = next(a for a in REGISTRY.values() if a.architectures)
    (tmp_path / "config.json").write_text(json.dumps(
        {"architectures": [arch.architectures[0]], "text_config": {"num_hidden_layers": 7}}))
    b = _ready(_state(args={"model": str(tmp_path)}, engine=None))
    assert b["family"] == arch.name and b["layers"] == 7


def test_a_setting_moves_only_its_field():
    base = _ready(_state())
    fixed = {k: base[k] for k in ("code", "family", "layers", "profile", "weights", "drafters",
                                  "rows", "tokenizer")}
    fixed["kv_fp8"] = base["decode"]["kv_fp8"]

    def block(**kw):
        return identity.block(_state(**kw), fixed, lambda: "served")

    assert block() == base
    assert _differ(base, block(max_len=4096)) == {"max_len"}
    assert _differ(base, block(temperature=0.7)) == {"decode"}
    assert _differ(base, block(args={"model": None, "parallel": 4})) == {"decode"}
    assert _differ(base, block(identity_override={"weights": "x"})) == {"weights"}


def test_a_part_that_fails_says_so():
    class Bad:
        def __call__(self, *a, **k):
            raise RuntimeError("busy")

        def __len__(self):
            return 1

    b = _ready(_state(tok=Bad()))
    assert b["tokenizer"] == "error: RuntimeError" and len(b["code"]) == 16


def test_the_route_takes_the_caller_rule_of_the_model_list():
    src = open(os.path.join(ROOT, "server", "app.py")).read()
    tree = ast.parse(src)
    get = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "do_GET"
               and "/v1/replica/identity" in ast.get_source_segment(src, n))

    def guards(path):
        node = next(n for n in ast.walk(get) if isinstance(n, ast.If)
                    and ast.get_source_segment(src, n.test) == f'path == "{path}"')
        tests = [ast.get_source_segment(src, n.test) for n in ast.walk(node) if isinstance(n, ast.If)]
        return [t for t in tests if "authorize" in t or "_allowed" in t]

    models, ident = guards("/v1/models"), guards("/v1/replica/identity")
    assert ident == ['STATE.get("proxy") is not None and not STATE["proxy"].authorize(self)[0]']
    assert [g.split("and", 1)[-1].strip() for g in models] == \
        [g.split("and", 1)[-1].strip() for g in ident], (models, ident)


# --------------------------------------------------------------------------- fake engines
def _need_torch():
    if importlib.util.find_spec("torch") is None:
        try:
            import pytest
            pytest.skip("the fake engine needs CPU torch")
        except ImportError:
            from scriptmode import Skip
            raise Skip("the fake engine needs CPU torch") from None


def _start(tmp: str, name: str, extra=(), env_extra=None):
    from ports import free_port
    port = free_port()
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", tmp),
           "PYTHONPATH": ROOT, "CUDA_VISIBLE_DEVICES": "", "QSE_ADMIN_TOKEN": ADMIN}
    env.update(env_extra or {})
    p = subprocess.Popen([sys.executable, "-c", LAUNCH, "--fake-engine", "--port", str(port),
                          "--served-model", "qwen38-spark-engine",
                          "--usage-ledger", os.path.join(tmp, f"{name}.sqlite3"), *extra],
                         cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return port, p


def _get(port, path, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read() or b"{}")


def _up(port, proc):
    end = time.time() + 90
    while time.time() < end:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            return
        except OSError:
            if proc.poll() is not None:
                raise AssertionError(proc.stdout.read())
            time.sleep(0.3)
    raise AssertionError("the fake engine did not come up in 90 s")


def _identity(port, headers=None) -> dict:
    end = time.time() + 60
    while time.time() < end:
        code, h, body = _get(port, "/v1/replica/identity", headers)
        if code == 200:
            return body
        assert code == 503 and body["error"]["type"] == "identity_pending", (code, body)
        assert h.get("Retry-After") == "1", h
        time.sleep(0.2)
    raise AssertionError("the identity was not ready in 60 s")


def test_fake_engines_compare_by_their_flags():
    _need_torch()
    tmp = tempfile.mkdtemp(prefix="replica-identity-")
    runs = {"a": ((), None), "b": ((), None), "window": (("--max-len", "4096"), None),
            "temp": (("--temperature", "0.7"), None),
            "weights": ((), {"FAKE_IDENTITY_WEIGHTS": "other"})}
    procs = {k: _start(tmp, k, *v) for k, v in runs.items()}
    try:
        for port, p in procs.values():
            _up(port, p)
        # the first call starts the fixed part: 503 identity_pending, then the block
        port = procs["a"][0]
        code, h, body = _get(port, "/v1/replica/identity")
        assert code == 503 and body["error"]["code"] == "identity_pending", (code, body)
        got = {k: _identity(port) for k, (port, _) in procs.items()}
        a = got["a"]
        assert tuple(a) == FIELDS and tuple(a["decode"]) == DECODE, a
        assert a["family"] == "fake" and a["layers"] is None and a["stage"] is None
        assert a["max_len"] == 8192 and a["decode"]["temperature"] == 0.0
        assert got["b"] == a
        assert _differ(a, got["window"]) == {"max_len"} and got["window"]["max_len"] == 4096
        assert _differ(a, got["temp"]) == {"decode"}
        assert _differ(a["decode"], got["temp"]["decode"]) == {"temperature"}
        assert _differ(a, got["weights"]) == {"weights"} and got["weights"]["weights"] == "other"
        # a second read is the same block (the fixed part is kept)
        assert _identity(procs["a"][0]) == a
    finally:
        for _, p in procs.values():
            p.kill()
            p.wait(10)


def test_fake_engine_route_answers_whoever_the_model_list_answers():
    _need_torch()
    tmp = tempfile.mkdtemp(prefix="replica-identity-")
    port, p = _start(tmp, "caller")
    try:
        _up(port, p)
        # an admin password is set; a caller through a reverse proxy with no token
        far = {"X-Forwarded-For": "192.0.2.7"}
        assert _get(port, "/v1/cache/stats", far)[0] == 401     # the admin rule holds here
        assert _get(port, "/v1/models", far)[0] == 200
        assert _identity(port, far)["identity"] == 1
    finally:
        p.kill()
        p.wait(10)


def _post(port, path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {ADMIN}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def test_fake_engine_route_is_gated_with_the_model_list_by_the_users_keys():
    _need_torch()
    tmp = tempfile.mkdtemp(prefix="replica-identity-")
    port, p = _start(tmp, "keys", ("--backends", os.path.join(tmp, "config.sqlite3")),
                     {"QSE_TEST": "1", "HOME": tmp,
                      "TANDEM_SECRETS_KEY": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s="})
    try:
        _up(port, p)
        far = {"X-Forwarded-For": "192.0.2.9", "Authorization": "Bearer nope"}
        # no user key yet: both answer anyone
        assert _get(port, "/v1/models", far)[0] == 200
        assert _identity(port, far)["identity"] == 1
        key = _post(port, "/v1/config/users", {"label": "t"})["key"]
        assert _get(port, "/v1/models", far)[0] == 401
        assert _get(port, "/v1/replica/identity", far)[0] == 401
        with_key = dict(far, Authorization=f"Bearer {key}")
        assert _get(port, "/v1/models", with_key)[0] == 200
        assert _identity(port, with_key)["identity"] == 1
    finally:
        p.kill()
        p.wait(10)


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
