"""TandemApp's dashboard contract (contracts/dashboard-v1): every example file validates against its
schema with tools/contract_check.py, and the validator catches drift. Moved here from the engine's
tests/test_dashboard_api.py in ENG-413 WP9; the engine checks its own answers against its own
contracts/engine-api-v1/dashboard there.

  1  every example in contracts/dashboard-v1 validates; the page's own names (session, error,
     gap) are among them                                        test_the_example_files_validate
  2  a request row with a wrong type, a missing field and an unknown one fails, naming each
                                                                test_the_validator_catches_drift

CPU, standard library only. Run: python tests/test_dashboard_examples.py
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from tools import contract_check  # noqa: E402

DASH = os.path.join(ROOT, "contracts", "dashboard-v1")


def test_the_example_files_validate():
    assert os.path.realpath(contract_check.ROOT) == os.path.realpath(DASH), contract_check.ROOT
    names = sorted(f[:-len(".example.json")] for f in os.listdir(DASH)
                   if f.endswith(".example.json"))
    assert set(names) >= {"summary", "usage", "requests", "system", "logs-json", "logs-line", "gap",
                          "error", "session"}, names
    for n in names:
        with open(os.path.join(DASH, f"{n}.example.json")) as f:
            errs = contract_check.check(json.load(f), n)
        assert not errs, (n, errs[:5])
    return f"{len(names)} examples valid"


def test_the_validator_catches_drift():
    with open(os.path.join(DASH, "requests.example.json")) as f:
        good = json.load(f)
    assert not contract_check.check(good, "requests")
    bad = json.loads(json.dumps(good))
    bad["requests"][0]["stream"] = "yes"
    del bad["requests"][0]["ttft_ms"]
    bad["requests"][0]["prompt_text"] = "no"
    errs = contract_check.check(bad, "requests")
    assert any("stream" in e for e in errs) and any("ttft_ms" in e for e in errs) \
        and any("prompt_text" in e for e in errs), errs
    return f"{len(errs)} errors named"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
