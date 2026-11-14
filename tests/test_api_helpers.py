"""ENG-413 WP2c (QA ENG-432): the small helpers the setup, the probe and the disk cache read are the
engine's own (server/api), and they answer as TandemApp's did.

tests/test_api_import_guard.py holds each copy's code to TandemApp's and its importers to server/api;
this file holds the answers, on the inputs the engine gives them.

The test matrix:

  1  the residency and roles seeds are the conductor's version 0, a fresh copy each call, the
     deciders seed the conductor's less its api (WP5); other kinds are not the engine's
                                                                    test_the_seeds_are_the_conductors
  2  the validator answers every conductor schema and the setup registry's model check line for
     line as TandemApp's                                           test_validate_answers_alike
  3  the secret refusal: every shape and plain code redacted alike, counts alike
                                                                    test_redact_answers_alike
  4  a directory inside a git work tree, beside one, and the root: the same answer
                                                                    test_in_repository_answers_alike
  5  chatd's address from QSE_CHAT_URL: the same host and port, the same error for a port that is
     no port                                                       test_chat_url_answers_alike
  6  Wren's index: the app texts given as extra knowledge make the same document list as before;
     without them the list is the engine's own documents in the same order; the scores and the
     search are TandemApp's index's                       test_wren_index_with_the_app_texts_as_extra
  7  a fresh process importing the switched modules loads no TandemApp module, and /health's
     chatd check reads the engine's address                          test_a_process_loads_no_app_module

CPU, standard library (6 needs PyYAML, as the setup registry does). Run: python tests/test_api_helpers.py
"""

from __future__ import annotations

import copy
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import server  # noqa: E402,F401  (serves TandemApp's modules from app/: the reference)


def test_the_seeds_are_the_conductors():
    from server.api import seeds
    from tandemapp.conductor import schema
    for kind in ("residency", "roles"):
        a, b = seeds.seed(kind), schema.seed(kind)
        assert a == b, kind
        assert a is not seeds.seed(kind), "a fresh copy"
        a["changed"] = True
        assert "changed" not in seeds.seed(kind), "the seed itself is not touched"
    # ENG-413 WP5: the deciders seed is the conductor's less its api (the extension's part)
    want = {k: v for k, v in schema.seed("deciders").items() if k != "api"}
    assert seeds.seed("deciders") == want and seeds.seed("router") is None
    assert seeds.TOKENIZER_NONE == schema.TOKENIZER_NONE
    return "residency, roles and deciders (less api) equal the conductor's, fresh copies"


def _broken(doc):
    """A few wrong versions of a document: a required key gone, a value of the wrong type, an
    unknown key, a list where an object is."""
    out = [[], {"unknown_key": 1}]
    if isinstance(doc, dict) and doc:
        k = sorted(doc)[0]
        out.append({x: v for x, v in doc.items() if x != k})
        out.append(dict(doc, **{k: "not-a-number"}))
        out.append(dict(doc, extra_key={"x": 1}))
    return out


def test_validate_answers_alike():
    from server.api.validate import validate
    from tandemapp.conductor import schema
    n = 0
    for kind, sch in sorted(schema.SCHEMAS.items()):
        doc = schema.seed(kind) if kind != "profiles" else schema.seed(kind, sorted(schema.PROFILES)[0])
        for v in [doc] + _broken(copy.deepcopy(doc)):
            assert validate(v, sch) == schema.validate(v, sch), kind
            n += 1
    from server.setup import registry
    for name, sch in sorted(vars(registry).items()):
        if isinstance(sch, dict) and sch.get("type") == "object" and "properties" in sch:
            for v in ({}, {"x": 1}, []):
                assert validate(v, sch) == schema.validate(v, sch), name
                n += 1
    return f"{n} documents answered alike"


SECRETS = (
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
    "-----BEGIN OPENSSH PRIVATE KEY-----\ncut off here",
    "Authorization: Bearer abcdefghijklmnop0123456789",
    "authorization: Basic dXNlcjpwYXNzd29yZA==",
    "AKIAABCDEFGHIJKLMNOP aws_secret_access_key = abcdefghijklmnopqrstuvwxyz0123456789ABCD",
    "ghp_" + "a" * 36, "github_pat_" + "b" * 30, "glpat-" + "c" * 20, "sk-ant-" + "d" * 24,
    "sk_live_" + "e" * 20, "xoxb-1234567890-abc", "AIza" + "f" * 35, "hf_" + "g" * 32,
    "npm_" + "h" * 36, "tk_" + "i" * 29,
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
    "postgres://user:hunter2xyz@db/x", "AccountKey=abcdef123456;", "password = 'hunter2xyz'",
    "--token s3cr3tvalue", "DB_PASSWORD=hunter2xyz",
    "token = tok(text)", "password: str", "MAX_TOKENS=128000", "", "plain words, nothing secret")


def test_redact_answers_alike():
    from server.api import redact as ours
    from tandemapp.conductor import redact as theirs
    assert ours.KINDS == theirs.KINDS
    texts = list(SECRETS) + ["\n".join(SECRETS)]
    for t in texts:
        assert ours.redact(t) == theirs.redact(t), t
    total = {}
    for t in texts:
        ours.merge(total, ours.redact(t)[1])
    assert set(total) == set(ours.KINDS), sorted(set(ours.KINDS) - set(total))
    return f"{len(texts)} texts, every one of {len(ours.KINDS)} kinds redacted alike"


def test_in_repository_answers_alike():
    from server.api.repository import in_repository
    from tandemapp.conductor import transcripts
    with tempfile.TemporaryDirectory(prefix="api-repo-") as d:
        repo = os.path.join(d, "work")
        deep = os.path.join(repo, "a", "b")
        os.makedirs(deep)
        os.makedirs(os.path.join(repo, ".git"))
        beside = os.path.join(d, "beside")
        os.makedirs(beside)
        for p in (deep, repo, beside, d, "/", os.path.join(d, "missing", "dir")):
            assert in_repository(p) == transcripts.in_repository(p), p
        assert in_repository(deep) == os.path.realpath(repo)
        assert in_repository(beside) is None or in_repository(beside) != os.path.realpath(repo)
    return "inside, beside and the root alike"


def test_chat_url_answers_alike():
    from server.api import chat_url
    from tandemapp import chat_forward
    envs = ({}, {"QSE_CHAT_URL": ""}, {"QSE_CHAT_URL": "http://127.0.0.1:9"},
            {"QSE_CHAT_URL": "http://chat.local"}, {"QSE_CHAT_URL": "https://[::1]:8443/x"},
            {"QSE_CHAT_URL": "unix-socket"})
    for env in envs:
        assert chat_url.target(env) == chat_forward.target(env), env
    for bad in ("http://127.0.0.1:x", "http://h:99999"):
        errs = []
        for f in (chat_url.target, chat_forward.target):
            try:
                f({"QSE_CHAT_URL": bad})
                errs.append(None)
            except ValueError as e:
                errs.append(type(e))
        assert errs[0] == errs[1] == ValueError, (bad, errs)
    return f"{len(envs)} addresses and 2 bad ports alike"


def test_wren_index_with_the_app_texts_as_extra():
    try:
        import yaml  # noqa: F401
    except ImportError:
        from scriptmode import Skip
        raise Skip("the setup registry reads model.yaml with PyYAML")
    from server.setup import knowledge as K
    from tandemapp.conductor.libsearch import TextIndex
    before = K.Knowledge()
    before.ensure()
    given = K.Knowledge(packages=K.PACKAGES, app_docs=K.APP_DOCS)
    given.ensure()
    assert given.docs == before.docs, "the app texts given as extra knowledge: the same list"
    app = set(K.PACKAGES) | set(K.APP_DOCS)
    assert app <= {d["path"] for d in before.docs}, "every app text is in the index"
    alone = K.Knowledge(packages=(), app_docs=())
    alone.ensure()
    assert alone.docs == [d for d in before.docs if d["path"] not in app], "the engine's own, in order"
    ref = TextIndex()
    ref.build(before.docs, K.WEIGHTS)
    qs = ("lookup store what does it do", "how many lanes fit in memory", "the proxy and its backends",
          "flashnext 3.05 longest conversation", "what is the conductor", "")
    for q in qs:
        assert before.text.scores(q) == ref.scores(q), q
    assert type(before.text).__module__ == "server.api.textindex"
    return f"{len(before.docs)} sections ({len(before.docs) - len(alone.docs)} from the app texts); " \
           f"{len(qs)} queries scored alike"


CHILD = """
import sys
sys.path.insert(0, sys.argv[1])
from server.api import chat_url, redact, repository, seeds, textindex, validate
from server import diskcache, mode, rolemem, decider_api
from server.setup import apply, community, knowledge, registry, wren
try:
    from server import probe
    print("probe", "loaded")
except ImportError as e:
    print("probe", "needs " + str(e.name))
seen = []
print("up", mode.chatd_up(clock=lambda: 1e9, connect=lambda t: seen.append(t) or True))
print("target", seen[0])
print("app", sorted(m for m in sys.modules if m.split(".")[0] in ("tandemapp", "proxy", "chatd")))
"""


def test_a_process_loads_no_app_module():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["QSE_CHAT_URL"] = "http://127.0.0.1:8611"
    with tempfile.TemporaryDirectory(prefix="api-helpers-") as cwd:
        p = subprocess.run([sys.executable, "-I", "-B", "-c", CHILD, ROOT], capture_output=True, text=True,
                           timeout=120, env=env, cwd=cwd)
    assert p.returncode == 0, (p.stdout[-2000:], p.stderr[-2000:])
    out = dict(ln.split(" ", 1) for ln in p.stdout.splitlines())
    assert out["app"] == "[]", out["app"]
    assert out["up"] == "True" and out["target"] == "('127.0.0.1', 8611)", out
    return f"the switched modules load no TandemApp module (probe {out['probe']}); chatd at {out['target']}"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
