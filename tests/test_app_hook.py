"""SRV-747: what the engine reaches of app/ through server/__init__.py until ENG-413 removes it.

  1  after `import server`, in a process whose path holds the repository and not app/,
     tandemapp, proxy and chatd load from app/, app/ is still not on sys.path, and a second
     import of server/__init__.py adds no second finder      test_server_serves_the_app_packages
  2  nothing else of app/ comes along: tools.tandem_eval (only app/tools has it) and the test
     helper tests.proxy_util (only app/tests has it) are not found, tools.contract_check is
     the engine's own                                        test_nothing_else_of_app_is_found
  3  in a tree without app/, `import server` installs nothing  test_no_app_no_finder

CPU, standard library only. Run: python tests/test_app_hook.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "app")


def _child(code: str, root: str = ROOT) -> list[str]:
    """`code` in `python -I -B` with only `root` of the tree on the path; its stdout lines."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    with tempfile.TemporaryDirectory(prefix="app-hook-") as cwd:
        p = subprocess.run([sys.executable, "-I", "-B", "-c", code, root], capture_output=True,
                           text=True, timeout=120, env=env, cwd=cwd)
    assert p.returncode == 0, (p.stdout[-2000:], p.stderr[-2000:])
    return p.stdout.splitlines()


PREAMBLE = ("import importlib, importlib.util, os, sys\n"
            "ROOT = sys.argv[1]\n"
            "sys.path.insert(0, ROOT)\n"
            "assert not any(os.path.realpath(p) == os.path.realpath(os.path.join(ROOT, 'app'))"
            " for p in sys.path), sys.path\n")


def test_server_serves_the_app_packages():
    out = _child(PREAMBLE +
                 "import server\n"
                 "import tandemapp, proxy, chatd\n"
                 "from tandemapp.conductor import schema\n"
                 "for m in (tandemapp, proxy, chatd, schema):\n"
                 "    print(m.__name__, os.path.realpath(m.__file__))\n"
                 "importlib.reload(server)\n"
                 "print('finders', sum(bool(getattr(f, 'tandem_app_finder', False))"
                 " for f in sys.meta_path))\n"
                 "print('app on path', any(os.path.realpath(p) == os.path.realpath("
                 "os.path.join(ROOT, 'app')) for p in sys.path))\n")
    app = os.path.realpath(APP)
    files = dict(line.split(" ", 1) for line in out[:4])
    assert all(f.startswith(app + os.sep) for f in files.values()), files
    assert files["tandemapp"] == os.path.join(app, "tandemapp", "__init__.py"), files
    assert out[4:] == ["finders 1", "app on path False"], out
    return f"{len(files)} modules from app/"


def test_nothing_else_of_app_is_found():
    assert os.path.isfile(os.path.join(APP, "tools", "tandem_eval.py"))
    assert not os.path.exists(os.path.join(ROOT, "tools", "tandem_eval.py"))
    assert os.path.isfile(os.path.join(APP, "tests", "proxy_util.py"))
    assert not os.path.exists(os.path.join(ROOT, "tests", "proxy_util.py"))
    out = _child(PREAMBLE +
                 "import server\n"
                 "print(importlib.util.find_spec('tools.tandem_eval'))\n"
                 "print(importlib.util.find_spec('tests.proxy_util'))\n"
                 "print(os.path.realpath(importlib.util.find_spec('tools.contract_check').origin))\n")
    engine = os.path.realpath(os.path.join(ROOT, "tools", "contract_check.py"))
    assert out == ["None", "None", engine], out


def test_no_app_no_finder():
    with tempfile.TemporaryDirectory(prefix="no-app-") as d:
        os.makedirs(os.path.join(d, "server"))
        shutil.copy(os.path.join(ROOT, "server", "__init__.py"), os.path.join(d, "server"))
        out = _child(PREAMBLE +
                     "import server\n"
                     "assert os.path.realpath(server.__file__).startswith("
                     "os.path.realpath(ROOT)), server.__file__\n"
                     "print('finders', sum(bool(getattr(f, 'tandem_app_finder', False))"
                     " for f in sys.meta_path))\n", root=d)
    assert out == ["finders 0"], out


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
