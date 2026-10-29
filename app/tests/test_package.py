"""P5, the installable package: pyproject.toml against the tree, the version mapping, the page in
an installed layout, and the `tandem-app` commands besides `serve`. The wheel itself is built and
installed into a clean venv by tools/package_check.sh (CI and the night pass); its isolation from
this machine's own TandemNode (SRV-745) is held here.

    python tests/test_package.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import re
import shutil
import stat
import sys
import tempfile
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from proxy import __main__ as cli_main  # noqa: E402
from proxy import _version  # noqa: E402

KEY = "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s="
# packages of the wheel that hold data alone (no .py): the built page and the shipped texts
DATA_ONLY = ("proxy.dashboard", "proxy.packages")


def _toml() -> dict:
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        return tomllib.load(f)


def test_pyproject_matches_the_tree():
    t = _toml()
    p, st = t["project"], t["tool"]["setuptools"]
    assert p["name"] == _version.DIST == "tandemapp"
    assert p["dynamic"] == ["version"] and \
        t["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "proxy._version.PEP440"}
    assert p["dependencies"] == ["cryptography>=42"], "the app needs cryptography alone"
    assert not any(d.split(">")[0].split("=")[0].strip().lower() in ("torch", "numpy",
                                                                    "transformers")
                   for d in p["dependencies"] + p["optional-dependencies"]["host"])
    assert p["scripts"]["tandem-app"] == "proxy.__main__:main"
    assert p["scripts"]["tandem-dash"] == "proxy.__main__:main_old_name"   # the old name, OPS-238
    assert p["scripts"]["tandem-node"] == "proxy.nodeagent:main"          # TandemNode, SRV-725
    assert callable(cli_main.main) and callable(cli_main.main_old_name)
    dirs = st.get("package-dir", {})
    assert not [x for x in st["packages"] if x.split(".")[0] in ("server", "engine")], \
        "the wheel carries no engine code"
    for pkg in st["packages"]:
        d = dirs.get(pkg, pkg.replace(".", "/"))
        assert os.path.isdir(os.path.join(ROOT, d)), (pkg, d)
        if not pkg.startswith(DATA_ONLY):
            assert any(f.endswith(".py") for f in os.listdir(os.path.join(ROOT, d))), pkg
    # every Python package under proxy/, chatd/ and tandemapp/ is listed, so nothing the code
    # imports is lost; tandemapp is a regular package (no namespace merges with another one)
    for top in ("proxy", "chatd", "tandemapp"):
        assert os.path.isfile(os.path.join(ROOT, top, "__init__.py")), top
        for d, subs, files in os.walk(os.path.join(ROOT, top)):
            subs[:] = [s for s in subs if s != "__pycache__"]
            if any(f.endswith(".py") for f in files):
                pkg = os.path.relpath(d, ROOT).replace(os.sep, ".")
                assert pkg in st["packages"], f"{pkg} is not in pyproject.toml's packages"
    for f in p["license-files"] + [p["readme"]]:
        assert os.path.exists(os.path.join(ROOT, f)), f
    assert os.path.exists(os.path.join(ROOT, "dashboard", "dist", "index.html"))
    # an sdist carries VERSION too: proxy/_version.py reads it beside pyproject.toml
    with open(os.path.join(ROOT, "MANIFEST.in")) as f:
        assert "include VERSION" in f.read().splitlines()


def test_the_built_page_is_in_the_wheel():
    """Every file of dashboard/dist is package data (sw.js was missing from every install until
    SRV-747), in setuptools' globs and in the node wheel's own reading of them
    (proxy/nodeinstall.py checkout_files)."""
    import fnmatch
    from proxy import nodeinstall
    st = _toml()["tool"]["setuptools"]
    pkg_of = {os.path.normpath(os.path.join(ROOT, d)): pkg
              for pkg, d in st["package-dir"].items() if pkg in st["packages"]}
    base = os.path.join(ROOT, "dashboard", "dist")
    files = [os.path.join(d, f) for d, _, fs in os.walk(base) for f in fs]
    assert os.path.join(base, "sw.js") in files and len(files) > 10, len(files)
    missing = [os.path.relpath(f, base) for f in files
               if not any(fnmatch.fnmatch(os.path.basename(f), g) for g in
                          st["package-data"].get(pkg_of.get(os.path.dirname(f)), []))]
    assert not missing, f"not in the wheel: {missing[:5]}"
    _, got = nodeinstall.checkout_files(ROOT)
    names = {w for w, _ in got}
    want = {"proxy/dashboard/" + os.path.relpath(f, base).replace(os.sep, "/") for f in files}
    assert want <= names, f"not in the node wheel: {sorted(want - names)[:5]}"
    assert {"tandemapp/__init__.py", "tandemapp/METRICS.md", "tandemapp/laya_kit/laya-drive.js",
            "tandemapp/conductor/monitor/policy.default.json", "chatd/__init__.py",
            "proxy/config.schema.json"} <= names
    assert not [n for n in names if n.split("/")[0] in ("server", "engine")]


def test_the_dash_image_copies_every_package():
    """ops/Dockerfile.dash builds the wheel: every source folder of pyproject.toml's packages must
    be COPYed and allowed by its .dockerignore (2026-10-20: chatd/ and packages/ were missing)."""
    st = _toml()["tool"]["setuptools"]
    dirs = st.get("package-dir", {})
    srcs = {dirs[p] if p in dirs else p.split(".")[0] for p in st["packages"]}

    def under(d, roots):
        return any(d == r or d.startswith(r + "/") for r in roots)

    with open(os.path.join(ROOT, "ops", "Dockerfile.dash")) as f:
        copied = [w[1].rstrip("/") for w in (ln.split() for ln in f)
                  if len(w) == 3 and w[0] == "COPY"]
    with open(os.path.join(ROOT, "ops", "Dockerfile.dash.dockerignore")) as f:
        allowed = [ln.strip()[1:].rstrip("/") for ln in f if ln.strip().startswith("!")]
    for d in sorted(srcs):
        assert under(d, copied), f"{d} is not COPYed in ops/Dockerfile.dash"
        assert under(d, allowed), f"{d} is not allowed in ops/Dockerfile.dash.dockerignore"


def test_the_shipped_texts_are_in_the_wheel():
    """The standalone proxy runs the conductor (P4), whose schema reads packages/seed.json at
    import: every file under packages/ must be package data of the wheel (2026-10-04 night pass:
    `serve` from the wheel died on a missing seed.json)."""
    import fnmatch
    st = _toml()["tool"]["setuptools"]
    assert "proxy.packages" in st["packages"]
    assert st["package-dir"]["proxy.packages"] == "packages"
    globs = st["package-data"]["proxy.packages"]
    base = os.path.join(ROOT, "packages")
    files = [os.path.relpath(os.path.join(d, f), base).replace(os.sep, "/")
             for d, _, fs in os.walk(base) for f in fs]
    assert "seed.json" in files and len(files) > 10
    def covered(rel):
        # setuptools: `*` within one directory, `**/` any depth below
        return any(fnmatch.fnmatch(rel, g) and (g.count("/") == rel.count("/") or "**" in g)
                   for g in globs)
    missing = [f for f in files if not covered(f)]
    assert not missing, f"not in the wheel: {missing[:5]}"


def test_the_shipped_texts_in_an_installed_layout():
    """A wheel puts packages/ at proxy/packages; a checkout keeps it at the root and wins."""
    from tandemapp.conductor import packages
    d = tempfile.mkdtemp(prefix="pkg-texts-")
    try:
        assert packages.shipped_root(d) == d, "nothing anywhere: the root, as before"
        os.makedirs(os.path.join(d, "proxy", "packages"))
        with open(os.path.join(d, "proxy", "packages", "seed.json"), "w") as f:
            f.write("{}")
        assert packages.shipped_root(d) == os.path.join(d, "proxy")
        os.makedirs(os.path.join(d, "packages"))
        with open(os.path.join(d, "packages", "seed.json"), "w") as f:
            f.write("{}")
        assert packages.shipped_root(d) == d, "a checkout wins"
    finally:
        shutil.rmtree(d, ignore_errors=True)
    assert packages.SHIPPED_ROOT == ROOT, "this checkout reads its own packages/"
    assert packages.Library()._dirs({"skill_dirs": ["{repo}/packages/skills"]}) == [
        os.path.join(ROOT, "packages", "skills")]


def test_version_mapping():
    assert _version.pep440("0.3.29-test") == "0.3.29.dev0"
    assert _version.pep440("0.4.0") == "0.4.0"
    assert _version.pep440("0.4.0-rc2") == "0.4.0rc2"
    assert _version.pep440("1.0.0-b1") == "1.0.0b1"
    try:
        _version.pep440("next")
        raise AssertionError("a bad VERSION passed")
    except ValueError:
        pass
    with open(os.path.join(ROOT, "VERSION")) as f:          # app/VERSION, TandemApp's own number
        assert _version.release() == f.read().strip()
    assert _version.PEP440 == _version.pep440(_version.release())


def test_the_page_in_an_installed_layout():
    d = tempfile.mkdtemp(prefix="pkg-")
    try:
        os.makedirs(os.path.join(d, "tandemapp"))
        os.makedirs(os.path.join(d, "proxy", "dashboard"))
        shutil.copy(os.path.join(ROOT, "tandemapp", "static.py"), os.path.join(d, "tandemapp"))
        spec = importlib.util.spec_from_file_location("static_installed",
                                                      os.path.join(d, "tandemapp", "static.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.default_dir() == os.path.join(d, "dashboard", "dist"), "no page: the old path"
        with open(os.path.join(d, "proxy", "dashboard", "index.html"), "w") as f:
            f.write("<html></html>")
        assert mod.default_dir() == os.path.join(d, "proxy", "dashboard")
        os.makedirs(os.path.join(d, "dashboard", "dist"))
        assert mod.default_dir() == os.path.join(d, "dashboard", "dist"), "a checkout wins"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _script_lines(path: str) -> list[str]:
    """A shell script's commands: continuation lines joined, stripped."""
    out, cur = [], ""
    with open(path) as f:
        for raw in f.read().splitlines():
            if raw.endswith("\\"):
                cur += raw[:-1] + " "
                continue
            out.append((cur + raw).strip())
            cur = ""
    return out


def _shell_function(lines: list[str], name: str) -> list[str]:
    """The commands of the top-level function `name() {` ... `}` among _script_lines' lines."""
    start = [i for i, s in enumerate(lines) if s.startswith(name + "() {")]
    assert len(start) == 1, f"package_check.sh defines {name}() {len(start)} times"
    end = lines.index("}", start[0])
    return [s for s in lines[start[0] + 1:end] if s and not s.startswith("#")]


def test_package_check_keeps_off_the_real_node():
    """SRV-745: tools/package_check.sh hashes the real node's claim and trust store (the home from
    the password database) before the pip steps, gives every command after them a home of its own
    and no TandemNode agent, keeps the conductor's documents in its temp dir, runs its own agent on
    a port of its own and stops every process it started before it compares the real state again,
    and fails at the exit when that state changed."""
    lines = _script_lines(os.path.join(ROOT, "tools", "package_check.sh"))
    code = [(i, s) for i, s in enumerate(lines) if s and not s.startswith("#")]
    codeset = {s for _, s in code}

    def first(pattern: str) -> int:
        for i, s in code:
            if re.search(pattern, s):
                return i
        raise AssertionError(f"package_check.sh has no line matching {pattern}")

    snap = first(r'^REAL=\$\(realnode snap\b')
    assert 'fail "reading the real TandemNode state"' in lines[snap], "an unread state is fatal"
    pips = [i for i, s in code if re.search(r'/bin/pip"? (-q )?install|-m pip install', s)]
    assert pips and snap < min(pips), "the real node is hashed before the pip steps"
    home = first(r'^export HOME="\$T/home" TANDEM_NODE_AGENT=off$')
    assert first(r'^mkdir -p "\$T/home"$') < home and home > max(pips), \
        "the home of its own and TANDEM_NODE_AGENT=off come after the pip steps"
    runs = [(i, s) for i, s in code if re.search(r'"\$(APP|OLD)" (version|check|serve)\b', s)]
    assert {m for _, s in runs for m in re.findall(r'"\$APP" (version|check|serve)\b', s)} == \
        {"version", "check", "serve"}, runs
    for i, s in runs:
        assert i > home, f"before HOME and TANDEM_NODE_AGENT=off: {s}"
        if re.search(r'"\$APP" (check|serve)\b', s):
            assert '--conductor-config "$T/conductor"' in s, f"no --conductor-config: {s}"
    # TandemNode from the wheel: its own state dir and port, loopback, no mDNS
    nodes = [(i, s) for i, s in code if re.search(r'/bin/tandem-node"? ', s)]
    assert nodes, "package_check.sh starts no tandem-node"
    for i, s in nodes:
        assert i > home and re.search(r'^"\$T/venv/bin/tandem-node" serve ', s), s
        for arg in ('--host 127.0.0.1 ', '--port $NPORT ', '--state-dir "$T/node" ', '--mdns off ',
                    '--virtual '):
            assert arg in s, f"tandem-node without {arg.strip()}: {s}"
    # its port is its own, set once (19500-19589), and nothing names the real agent's 7700
    nport = [s for _, s in code if re.search(r'\bNPORT=', s)]
    assert len(nport) == 1 and re.search(r'(^|; )NPORT=\$\(\(19500 \+ \$\$ % 90\)\)$', nport[0]), \
        nport
    assert not [s for _, s in code if "7700" in s], "package_check.sh names port 7700"
    # the identity comes from that agent: its port and its own state dir's loopback secret
    assert [s for _, s in code if s.startswith('"$T/venv/bin/python" -I - "$NPORT" '
                                               '"$T/node/secret" "$VER" ')], "no identity check"
    for want in ('port, secret, want = sys.argv[1], sys.argv[2], sys.argv[3]',
                 'node = nodesource.AgentNode(f"http://127.0.0.1:{port}",',
                 'secret=lambda: nodetrust.load_secret(secret))'):
        assert want in codeset, f"the identity check lost: {want}"
    # stop() halts every process started in the background, the agent included
    started = {m.group(1) for _, s in code for m in [re.fullmatch(r'(\w+)=\$!', s)] if m}
    stops = [s for _, s in code if s.startswith("stop() {")]
    assert started >= {"FAKE", "SRV", "NODE"} and len(stops) == 1, (started, stops)
    halted = set(re.findall(r'\bhalt "\$(\w+)";', stops[0]))
    assert started <= halted, f"stop() leaves running: {sorted(started - halted)}"
    # the import audit, from the temp dir with -I: a server, engine, torch or numpy module or a
    # findable `server` fails it
    assert [s for _, s in code if s.startswith('"$T/venv/bin/python" -I - >"$OUT/imports.log" ')
            and '|| fail "the installed app reaches the engine' in s], "no import audit"
    for want in ('for name in ("proxy.__main__", "proxy.app", "proxy.nodeagent", "chatd", '
                 '"tandemapp.conductor.routes"):',
                 'bad = sorted(m for m in sys.modules if m.split(".")[0] in ("server", "engine", '
                 '"torch", "numpy"))',
                 'spec = importlib.util.find_spec("server")',
                 'sys.exit(1 if bad or spec is not None else 0)'):
        assert want in codeset, f"the import audit lost: {want}"
    # the real node: the home from the password database (not $HOME), and a change fails
    for want in ("home = pwd.getpwuid(os.getuid()).pw_dir", "sys.exit(1 if bad else 0)"):
        assert want in codeset, f"the real-node comparison lost: {want}"
    real = _shell_function(lines, "real_check")
    same = [k for k, s in enumerate(real)
            if s == """if printf '%s' "$REAL" | realnode same >"$T/real.txt" 2>&1; then"""]
    assert same and "stop" in real[:same[0]], "the processes stop before the comparison"
    assert real[-2:] == ['echo "FAIL the real TandemNode state changed" | tee -a '
                         '"$OUT/summary.txt"', "return 1"], real
    # at the exit: a failure stays a failure, the real node is compared, and only the temp dir
    # from mktemp's pattern is removed
    assert "trap finish EXIT" in codeset, "no trap finish EXIT"
    guarded = 'case "$T" in */tandem-pkg-??????) rm -rf "$T" ;; esac'
    assert _shell_function(lines, "finish") == [
        "local rc=$?", "real_check || rc=1", "stop", guarded, 'exit "$rc"'], \
        "finish keeps the exit code, compares the real node and removes only its temp dir"
    assert [s for _, s in code if "rm -rf" in s] == [guarded], "rm -rf only behind the pattern"
    assert first(r'^T=\$\(mktemp -d "\$\{TMPDIR:-/tmp\}/tandem-pkg-XXXXXX"\)') < snap
    assert code[-1][1].startswith('echo "PASS all') and \
        code[-2][1] == "real_check || exit 1", "the real node is compared before PASS all"


def _run(argv, env) -> tuple[int, str]:
    old = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            try:
                cli_main.main(argv)
                code = 0
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        os.environ.clear()
        os.environ.update(old)
    return code, buf.getvalue()


def test_check_secrets_and_backends_commands():
    d = tempfile.mkdtemp(prefix="pkg-cli-")
    try:
        db = os.path.join(d, "conf", "config.sqlite3")
        base = {"HOME": d, "PATH": os.environ.get("PATH", "")}
        envf = os.path.join(d, "app.env")
        with open(envf, "w") as f:
            f.write("TANDEM_BACKENDS=ds,v1\nTANDEM_BACKEND_DS_KIND=deepseek\n"
                    "TANDEM_BACKEND_DS_KEY=sk-fixture-CLI-0001\n"
                    "TANDEM_BACKEND_V1_KIND=generic\nTANDEM_BACKEND_V1_URL=http://127.0.0.1:9/v1\n"
                    "TANDEM_BACKEND_V1_HOST=collector\n"
                    "TANDEM_BACKEND_V1_HOST_URL=http://127.0.0.1:9108\n"
                    f"TANDEM_SECRETS_KEY={KEY}\n")
        code, out = _run(["check", "--env-file", envf, "--config-db", db], base)
        assert code == 0, out
        assert "backends 2" in out and "host collector http://127.0.0.1:9108" in out, out
        assert "master key from env" in out and "OK" in out.splitlines()[-1]
        assert "sk-fixture-CLI-0001" not in out
        assert not os.path.exists(db), "check writes nothing"
        code, out = _run(["check", "--env-file", envf, "--config-db", db, "--host", "0.0.0.0"],
                         base)
        assert code == 0 and "WARN    bound beyond loopback" in out, out
        with open(envf, "a") as f:
            f.write("TANDEM_BACKEND_V1_HOST_DCGM=x\n")
        code, out = _run(["check", "--env-file", envf, "--config-db", db], base)
        assert code == 1 and "TANDEM_BACKEND_V1_HOST_DCGM" in out, out
        # secrets: init writes the key file once, at 0600; status reads it
        code, out = _run(["secrets", "status", "--config-db", db], base)
        assert code == 0 and "none yet" in out, out
        code, out = _run(["secrets", "init", "--config-db", db, "--no-keychain"], base)
        kf = os.path.join(d, "conf", "secrets.key")
        assert code == 0 and os.path.exists(kf), out
        assert stat.S_IMODE(os.stat(kf).st_mode) == 0o600
        first = open(kf).read()
        code, out = _run(["secrets", "init", "--config-db", db, "--no-keychain"], base)
        assert code == 0 and "nothing written" in out and open(kf).read() == first
        # backends list on an existing database, read through a copy
        from proxy import config as config_mod
        cfg = config_mod.ConfigDB(db, env={}, use_keychain=False).open()
        cfg.create_backend({"id": "local", "kind": "generic", "url": "http://127.0.0.1:8100/v1",
                            "key": "sk-fixture-CLI-0002"})
        cfg.close()
        before = os.path.getmtime(db)
        code, out = _run(["backends", "list", "--config-db", db], base)
        assert code == 0 and out.startswith("local ") and "key …0002 (database)" in out, out
        assert "sk-fixture-CLI-0002" not in out and os.path.getmtime(db) == before
        code, out = _run(["version"], base)
        assert code == 0 and out.startswith(f"tandemapp {_version.release()} "), out
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    from proxy_util import run
    run(globals())
