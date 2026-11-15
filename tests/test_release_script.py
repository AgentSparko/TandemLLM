"""tools/release.sh (OPS-223) on a scratch repository with a local bare remote: no network, no GitHub, no
box. bump writes the version into VERSION and the pointer lines and leaves the prose;
preflight passes on the bumped tree and names what is missing on the old one; a dry run prints the tag and push commands and changes no
ref; the milestone section closes vX.Y.Z and opens the next patch through a stand-in gh. Under
/bin/bash where it is bash 3.2 (macOS), else the bash on PATH. tools/release_box_gate.sh's live steps,
smoke and loose, with stand-in scripts and a /health on loopback: each runs only while /health names the
version, with the smoke key in its environment and on no command line; --skip leaves each out. Its
suite step runs the engine's list (tools/engine_tests.py: no pair test) and fails when a file does.

Run: python tests/test_release_script.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tools", "release.sh")
GATE = os.path.join(ROOT, "tools", "release_box_gate.sh")
BASH = "/bin/bash" if os.path.exists("/bin/bash") else (shutil.which("bash") or "bash")
ID = ["-c", "user.name=Release Test", "-c", "user.email=release@example.invalid"]

FILES = {
    "VERSION": "1.2.3\n",
    "install.sh": '#!/bin/bash\n# The engine release. v1.2.3 adds a thing.\nTANDEM_REF="${TANDEM_REF:-v1.2.3}"\n',
    "README.md": ("curl -fsSLO https://raw.githubusercontent.com/o/r/v1.2.3/install.sh\n\n"
                  "It clones the engine at the tag `v1.2.3` into `~/T/src`, via install.sh.\n\n"
                  "Where the tag `v1.2.3` is missing, the install.sh step stops.\n\n"
                  "| `--ref REF` | the engine version, default `v1.2.3`; |\n\n"
                  "The current release is `v1.2.3`. `v1.2.2` fixed install.sh.\n"),
    "docs/setup.md": "curl -fsSLO https://raw.githubusercontent.com/o/r/v1.2.3/install.sh\nthe tag `v1.2.3`.\n",
    "manual/preamble.tex": "\\newcommand{\\version}{1.2.3}\n",
    "manual/chapters/install-engine.tex": "curl -fsSLO https://raw.githubusercontent.com/o/r/v1.2.3/install.sh\n",
    "manual/chapters/troubleshooting.tex": "no tag, branch or commit 'v1.2.3'; \\code{v1.2.3} by default\n",
    "manual/generated/install-flags.tex": "\\texttt{-{}-ref REF} & the engine release (default v1.2.3) \\\\\n",
    "manual/chapters/changelog.tex": "1.2.3 & 10-20 & a thing \\\\\n\\section{1.2.3, a thing}\n",
    "CHANGELOG.md": "# Changelog\n\n## 1.2.3, 2026-10-20\n\nA thing.\n",
}


def _git(cwd, *args, check=True):
    return subprocess.run(["git", *ID, *args], cwd=cwd, check=check, capture_output=True, text=True)


def _repo():
    d = tempfile.mkdtemp(prefix="release-test-")
    remote, work = os.path.join(d, "remote.git"), os.path.join(d, "work")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", remote], check=True)
    os.makedirs(work)
    _git(work, "init", "-q", "-b", "main")
    for rel, text in FILES.items():
        os.makedirs(os.path.dirname(os.path.join(work, rel)) or work, exist_ok=True)
        with open(os.path.join(work, rel), "w") as fh:
            fh.write(text)
    os.makedirs(os.path.join(work, "tools"))
    shutil.copy(SCRIPT, os.path.join(work, "tools", "release.sh"))
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "1.2.3")
    _git(work, "tag", "-a", "v1.2.3", "-m", "TandemLLM 1.2.3")
    _git(work, "remote", "add", "origin", remote)
    _git(work, "push", "-q", "origin", "main", "v1.2.3")
    return d, work


def _release(work, *args):
    env = dict(os.environ, RELEASE_NOTES_CHECK="", RELEASE_NOTES_DENY="", GIT_CONFIG_COUNT="2",
               GIT_CONFIG_KEY_0="user.name", GIT_CONFIG_VALUE_0="Release Test",
               GIT_CONFIG_KEY_1="user.email", GIT_CONFIG_VALUE_1="release@example.invalid")
    return subprocess.run([BASH, os.path.join(work, "tools", "release.sh"), *args, "--repo", "o/r"],
                          cwd=work, capture_output=True, text=True, env=env, timeout=120)


def _read(work, rel):
    with open(os.path.join(work, rel)) as fh:
        return fh.read()


def test_bump_then_preflight_then_a_dry_run():
    d, work = _repo()
    try:
        r = _release(work, "1.2.4", "--only", "preflight")
        assert r.returncode != 0, r.stdout
        assert "FAIL  VERSION says 1.2.3" in r.stdout and "still points at v1.2.3" in r.stdout, r.stdout

        r = _release(work, "1.2.4", "--only", "bump")
        assert r.returncode == 0, r.stdout + r.stderr
        assert _read(work, "VERSION") == "1.2.4\n" and not os.path.exists(os.path.join(work, "app"))
        inst = _read(work, "install.sh")
        assert 'TANDEM_REF="${TANDEM_REF:-v1.2.4}"' in inst and "# The engine release. v1.2.3" in inst, inst
        readme = _read(work, "README.md")
        assert "/v1.2.4/install.sh" in readme and "default `v1.2.4`" in readme, readme
        assert "at the tag `v1.2.4`" in readme and "Where the tag `v1.2.4`" in readme, readme
        assert "The current release is `v1.2.3`. `v1.2.2` fixed install.sh." in readme, "the paragraph is prose"
        assert "\\version}{1.2.4}" in _read(work, "manual/preamble.tex")
        assert "v1.2.3" not in _read(work, "manual/chapters/troubleshooting.tex")
        assert "(default v1.2.4)" in _read(work, "manual/generated/install-flags.tex")

        # the author's part: the release paragraph, the changelogs; then the release commit
        with open(os.path.join(work, "README.md"), "w") as fh:
            fh.write(readme.replace("The current release is `v1.2.3`.", "The current release is `v1.2.4`. In `v1.2.3` a thing."))
        with open(os.path.join(work, "CHANGELOG.md"), "w") as fh:
            fh.write("# Changelog\n\n## 1.2.4, 2026-10-22\n\nAnother thing.\n\n## 1.2.3, 2026-10-20\n\nA thing.\n")
        with open(os.path.join(work, "manual/chapters/changelog.tex"), "a") as fh:
            fh.write("1.2.4 & 10-22 & another thing \\\\\n\\section{1.2.4, another thing}\n")
        notes = os.path.join(d, "notes.md")
        with open(notes, "w") as fh:
            fh.write("Release notes.\n")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", "Release 1.2.4")

        r = _release(work, "1.2.4", "--only", "preflight", "--notes", notes)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "PREFLIGHT: all checks pass" in r.stdout and "previous release v1.2.3" in r.stdout, r.stdout
        assert "app" + "/VERSION" not in r.stdout, r.stdout          # ENG-413 WP14: no such file

        head = _git(work, "rev-parse", "HEAD").stdout.strip()
        r = _release(work, "1.2.4", "--only", "preflight,tag,push", "--notes", notes, "--dry-run")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "+ git -C" in r.stdout and "tag -a v1.2.4 -m TandemLLM 1.2.4 HEAD" in r.stdout, r.stdout
        assert "push origin HEAD:refs/heads/main refs/tags/v1.2.4:refs/tags/v1.2.4" in r.stdout, r.stdout
        assert _git(work, "tag", "-l", "v1.2.4").stdout.strip() == "", "a dry run made the tag"
        assert _git(work, "ls-remote", "origin", "main").stdout.split()[0] != head, "a dry run pushed"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_trailer_stops_the_preflight():
    d, work = _repo()
    try:
        _release(work, "1.2.4", "--only", "bump")
        with open(os.path.join(work, "CHANGELOG.md"), "w") as fh:
            fh.write("# Changelog\n\n## 1.2.4, 2026-10-22\n\nAnother thing.\n")
        readme = _read(work, "README.md").replace("current release is `v1.2.3`", "current release is `v1.2.4`")
        with open(os.path.join(work, "README.md"), "w") as fh:
            fh.write(readme)
        with open(os.path.join(work, "manual/chapters/changelog.tex"), "a") as fh:
            fh.write("1.2.4 & 10-22 & x \\\\\n\\section{1.2.4, x}\n")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", "Release 1.2.4\n\nCo-Authored-By: Someone <someone@example.invalid>")
        r = _release(work, "1.2.4", "--only", "preflight")
        assert r.returncode != 0 and "carries a co-author or session line" in r.stdout, r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_knob_name_in_a_body_is_no_session_line():
    d, work = _repo()
    try:
        _release(work, "1.2.4", "--only", "bump")
        with open(os.path.join(work, "CHANGELOG.md"), "w") as fh:
            fh.write("# Changelog\n\n## 1.2.4, 2026-10-22\n\nAnother thing.\n")
        readme = _read(work, "README.md").replace("current release is `v1.2.3`", "current release is `v1.2.4`")
        with open(os.path.join(work, "README.md"), "w") as fh:
            fh.write(readme)
        with open(os.path.join(work, "manual/chapters/changelog.tex"), "a") as fh:
            fh.write("1.2.4 & 10-22 & x \\\\\n\\section{1.2.4, x}\n")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", "Release 1.2.4\n\nRows for --session-spill-idle-every-s,\nSESSION_SPILL_IDLE and session_id.")
        r = _release(work, "1.2.4", "--only", "preflight")
        assert "carries a co-author or session line" not in r.stdout, r.stdout
        _git(work, "commit", "-q", "--allow-empty", "-m", "One more\n\nAgent-Session: 0123")
        r = _release(work, "1.2.4", "--only", "preflight")
        assert r.returncode != 0 and "carries a co-author or session line" in r.stdout, r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)

FAKE_GH = """#!/bin/sh
# a stand-in for gh: the milestone list from $FAKE_GH_LIST, every call logged to $FAKE_GH_LOG
echo "$*" >> "$FAKE_GH_LOG"
case "$*" in
    *"milestones?state=all"*) cat "$FAKE_GH_LIST" ;;
esac
"""


def _milestone_run(work, listing, *args):
    d = os.path.dirname(work)
    bin_dir, log, lst = os.path.join(d, "bin"), os.path.join(d, "gh.log"), os.path.join(d, "milestones")
    os.makedirs(bin_dir, exist_ok=True)
    with open(os.path.join(bin_dir, "gh"), "w") as fh:
        fh.write(FAKE_GH)
    os.chmod(os.path.join(bin_dir, "gh"), 0o755)
    with open(lst, "w") as fh:
        fh.write(listing)
    open(log, "w").close()
    env = dict(os.environ, PATH=bin_dir + os.pathsep + os.environ["PATH"], FAKE_GH_LOG=log, FAKE_GH_LIST=lst)
    r = subprocess.run([BASH, os.path.join(work, "tools", "release.sh"), "1.2.4", "--only", "milestone",
                        "--repo", "o/r", *args], cwd=work, capture_output=True, text=True, env=env, timeout=60)
    with open(log) as fh:
        return r, fh.read().splitlines()


def test_the_milestone_section_closes_the_release_and_opens_the_next_patch():
    d, work = _repo()
    try:
        listing = "3 open v1.2.4\n4 open v1.3.0\n5 open Later\n1 closed Nemotron community packs\n"
        r, calls = _milestone_run(work, listing, "--dry-run")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "+ gh api -X PATCH repos/o/r/milestones/3 -f state=closed" in r.stdout, r.stdout
        assert "+ gh api -X POST repos/o/r/milestones -f title=v1.2.5" in r.stdout, r.stdout
        assert all("-X" not in c for c in calls), ("a dry run changed a milestone", calls)

        r, calls = _milestone_run(work, listing)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "api -X PATCH repos/o/r/milestones/3 -f state=closed" in calls, calls
        assert any(c.startswith("api -X POST repos/o/r/milestones -f title=v1.2.5") for c in calls), calls

        r, calls = _milestone_run(work, "3 closed v1.2.4\n6 open v1.2.5\n")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "already closed" in r.stdout and "milestone v1.2.5 exists" in r.stdout, r.stdout
        assert all("-X" not in c for c in calls), calls

        r, calls = _milestone_run(work, "")
        assert r.returncode == 0 and "nothing to close" in r.stdout, r.stdout
        assert any("title=v1.2.5" in c for c in calls), calls
    finally:
        shutil.rmtree(d, ignore_errors=True)


# a stand-in for tools/client_smoke.py and tools/loose_ends_live.py: it notes how it ran
GATE_STUB = r'''import json, os, sys
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ran.jsonl"), "a") as f:
    f.write(json.dumps({"tool": os.path.basename(__file__), "argv": sys.argv[1:],
                        "key": os.environ.get("QSE_API_KEY")}) + "\n")
print("stub ok")
'''


def _health():
    """A server on loopback whose /health names `srv.version`; `srv.hits` lists the paths asked."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            srv.hits.append(self.path)
            data = json.dumps({"status": "ok", "version": srv.version}).encode()
            self.send_response(200 if self.path == "/health" else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.version, srv.hits = "", []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_the_box_gate_runs_smoke_and_loose_on_the_candidate_with_the_key():
    d = tempfile.mkdtemp(prefix="release-gate-")
    srv = _health()
    try:
        tree = os.path.join(d, "tree")
        os.makedirs(os.path.join(tree, "tools"))
        shutil.copy(GATE, os.path.join(tree, "tools", "release_box_gate.sh"))
        for name in ("client_smoke.py", "loose_ends_live.py"):
            with open(os.path.join(tree, "tools", name), "w") as fh:
                fh.write(GATE_STUB)
        ran = os.path.join(tree, "ran.jsonl")
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def gate(skip, key=None):
            if os.path.exists(ran):
                os.remove(ran)
            if key is not None:
                with open(os.path.join(tree, ".smoke-key"), "w") as fh:
                    fh.write(key)
            out = tempfile.mkdtemp(dir=d)
            r = subprocess.run([BASH, "tools/release_box_gate.sh", "--version", "1.2.3", "--py",
                                sys.executable, "--smoke-base", base + "/v1", "--skip", skip,
                                "--out", out], cwd=tree, capture_output=True, text=True, timeout=120)
            runs = [json.loads(x) for x in open(ran)] if os.path.exists(ran) else []
            return r, _read(out, "SUMMARY").splitlines(), runs, out

        # another version serves: neither live step runs, each says why; --skip smoke keeps loose's rule
        srv.version = "1.2.2"
        r, summary, runs, _ = gate("suite,setup")
        want = f"FAIL the server at {base} runs '1.2.2', not 1.2.3: deploy the candidate first"
        assert r.returncode == 1 and runs == [], (r.stdout, r.stderr, runs)
        assert summary == ["suite  skipped", "setup  skipped", f"smoke  {want} (or --skip smoke)",
                           f"loose  {want} (or --skip loose)"], summary
        r, summary, runs, _ = gate("suite,setup,smoke")
        assert r.returncode == 1 and runs == [], (r.stdout, r.stderr, runs)
        assert summary[2:] == ["smoke  skipped", f"loose  {want} (or --skip loose)"], summary
        # the candidate (a local label is its release): both run, with the key in the environment
        srv.version = "1.2.3+build"
        r, summary, runs, out = gate("suite,setup", key="k-gate-test")
        assert r.returncode == 0, (r.stdout, r.stderr, summary)
        assert summary[2:4] == ["smoke  PASS conductor: stub ok (1.2.3+build)",
                                "smoke  PASS raw: stub ok (1.2.3+build)"], summary
        assert summary[4].startswith("loose  PASS ") and \
            summary[4].endswith(f" s ({out}/loose.json; 1.2.3+build)"), summary
        assert [x["tool"] for x in runs] == ["client_smoke.py", "client_smoke.py",
                                             "loose_ends_live.py"], runs
        assert runs[2]["argv"] == ["--base", base, "--out", f"{out}/loose.json"], runs
        assert runs[1]["argv"][:3] == ["--base", base + "/v1", "--x-tandem"], runs
        assert all(x["key"] == "k-gate-test" for x in runs), runs
        assert not any("k-gate-test" in a for x in runs for a in x["argv"]), runs
        assert not os.path.exists(os.path.join(tree, ".smoke-key")), "the gate keeps the key"
        # both skipped: /health is not asked
        hits = len(srv.hits)
        r, summary, runs, _ = gate("suite,setup,smoke,loose")
        assert r.returncode == 0 and runs == [] and len(srv.hits) == hits, (summary, srv.hits)
        assert summary[2:] == ["smoke  skipped", "loose  skipped"], summary
    finally:
        srv.shutdown()
        shutil.rmtree(d, ignore_errors=True)


# a stand-in for tests/script_suite.py: one file, and it fails when its text says FAIL
ENGINE_RUNNER = r'''import sys
sys.exit(1 if "FAIL" in open(sys.argv[1]).read() else 0)
'''


def test_the_box_gate_suite_runs_the_engines_list():
    d = tempfile.mkdtemp(prefix="release-gate-suite-")
    try:
        tree = os.path.join(d, "tree")
        for sub in ("tools", "tests"):
            os.makedirs(os.path.join(tree, sub))
        shutil.copy(GATE, os.path.join(tree, "tools", "release_box_gate.sh"))
        shutil.copy(os.path.join(ROOT, "tools", "engine_tests.py"), os.path.join(tree, "tools", "engine_tests.py"))
        for rel, text in (("tests/script_suite.py", ENGINE_RUNNER), ("tests/test_a.py", "pass\n"),
                          ("tests/test_b.py", "pass\n"), ("tests/test_p.py", "FAIL\n"),
                          ("tests/test-ledger.json", json.dumps({"pair_suite": {"files": ["tests/test_p.py"]}}))):
            with open(os.path.join(tree, rel), "w") as fh:
                fh.write(text)

        env = dict(os.environ)
        if shutil.which("timeout") is None:                        # macOS
            fbin = os.path.join(d, "bin")
            os.makedirs(fbin)
            with open(os.path.join(fbin, "timeout"), "w") as fh:
                fh.write('#!/bin/sh\nshift\nexec "$@"\n')
            os.chmod(os.path.join(fbin, "timeout"), 0o755)
            env["PATH"] = fbin + os.pathsep + env["PATH"]

        def suite():
            out = tempfile.mkdtemp(dir=d)
            r = subprocess.run([BASH, "tools/release_box_gate.sh", "--version", "1.2.3", "--py", sys.executable,
                                "--jobs", "2", "--skip", "setup,smoke,loose", "--out", out],
                               cwd=tree, capture_output=True, text=True, timeout=120, env=env)
            # macOS's wc pads its count; the box's does not
            return r, [re.sub(r"=\s+", "=", ln) for ln in _read(out, "SUMMARY").splitlines()]

        # the pair test (it would fail here) is not the engine's: left out
        r, summary = suite()
        assert r.returncode == 0, (r.stdout, r.stderr, summary)
        assert summary[0].startswith("suite  PASS files=2 ok=2 skipped=0 failed=0 "), summary
        with open(os.path.join(tree, "tests/test_b.py"), "w") as fh:
            fh.write("FAIL\n")
        r, summary = suite()
        assert r.returncode == 1, (r.stdout, r.stderr, summary)
        assert summary[0].startswith("suite  FAIL files=2 ok=1 skipped=0 failed=1 "), summary
        assert summary[1].strip().startswith("FAIL test_b "), summary
        assert not any("app" + "/" in ln for ln in summary), summary
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return "the engine's two files run, the pair test left out; a failing file fails the step"


def test_the_scripts_parse():
    for f in ("tools/release.sh", "tools/release_box_gate.sh"):
        r = subprocess.run([BASH, "-n", os.path.join(ROOT, f)], capture_output=True, text=True)
        assert r.returncode == 0, (f, r.stderr)


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
