#!/usr/bin/env python3
"""Generate the docs' reference tables from the code, so they stay in sync with it.

    python3 tools/gen_reference.py              # rewrites every generated block under docs/
    python3 tools/gen_reference.py --root DIR   # read the code of another checkout
    python3 tools/gen_reference.py --check      # exit 1 when a committed block is stale
                                                # or the HTTP reference misses something

A page is written by hand around the blocks this tool owns (docs/reference/*.md, and the verify
price tables in docs/speculative-decoding.md):

    <!-- generated: NAME by tools/gen_reference.py; do not edit by hand -->
    ...
    <!-- /generated -->

The HTTP reference (routes, headers, errors: tools/http_ref.py) is written by hand and checked
against the code: a path a handler tests, a header name or an error type the code uses that the
reference lacks, or a row the code no longer has, is printed, and the run exits 2 (1 with --check).

It never imports the project: every table comes from parsing the source with `ast` (the server's
argument parser, the engine's settings registry, the metric registrations, the usage ledger's
schema) or from plain text (install.sh's usage text, ops/serve.env, the verify price tables).

The engine's code only (ENG-413): nothing here reads TandemApp's tree. The QWEN38_* knobs the engine extension
reads (contracts/engine-ext-v1/knobs.json) are TandemApp's rows, and so are the proxy's flags, the
TANDEM_* variables TandemApp documents, its metrics and its routes: TandemApp's docs have them,
from its own copy of this tool. Standard library only, Python 3.9 or newer.
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# --------------------------------------------------------------------------- text helpers

def esc(s: str) -> str:
    """Plain text for a Markdown table cell."""
    t = re.sub(r"\s+", " ", str(s))
    return t.replace("\\", "\\\\").replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;").replace("*", "\\*").replace("_", "\\_")


def code(s: str) -> str:
    """An inline code span (a cell's pipes escaped; a backtick inside widens the fence)."""
    t = " ".join(str(s).split()).replace("|", "\\|")
    if not t:
        return ""
    fence = "``" if "`" in t else "`"
    pad = " " if fence == "``" else ""
    return fence + pad + t + pad + fence


def table(caption: str, heads: list, rows: list) -> str:
    """A Markdown table under its caption as a heading (an anchor for links)."""
    out = ["### " + caption, "", "| " + " | ".join(heads) + " |", "|" + "-|" * len(heads)]
    for r in rows:
        out.append("| " + " | ".join(c if c else " " for c in r) + " |")
    return "\n".join(out) + "\n"


# Words and references that belong to the development history, not to a reference table.
_TICKET = re.compile(r"\b(?:[A-Z]{2,4}\d?-[A-Z]?\d+[a-z]?|[A-Z]\d{1,2}(?:\.\d)?)\b")
_DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")


def clean(text: str, limit: int = 230) -> str:
    """The first sentence or two of a help text, without history references."""
    t = " ".join(str(text or "").split())
    if not t:
        return ""
    t = t.replace(chr(0x2014), ", ").replace(" -- ", ", ").lstrip("/")
    t = re.sub(r"\b(?:of|on|since|from|until)\s+20\d\d-\d\d-\d\d", "", t)
    t = re.sub(r"\b0\.\d+\.\d+(?:-test)?\b:?\s*", "", t)
    t = re.sub(r"\bsince phase \d+,?\s*", "", t)
    t = re.sub(r"^\([^)]*\)\s*:?\s*", "", t)
    # parentheticals that point at the development history go whole
    def drop(m: re.Match) -> str:
        inner = m.group(0)
        if (_TICKET.search(inner) or _DATE.search(inner) or ":8000" in inner or re.search(r"\bown(?:er)\b", inner)
                or "phase" in inner or "test)" in inner or "-test" in inner):
            return ""
        return inner
    for _ in range(2):
        t = re.sub(r"\([^()]*\)", drop, t)
    t = _TICKET.sub("", t)
    t = _DATE.sub("", t)
    t = t.replace("the :8000 service", "the service").replace(":8000", "the service port")
    t = re.sub(r"\bloss(?:less)\b", "exact under the agreement rule", t, flags=re.I)
    t = re.sub(r"\bLOSSY\.\s*", "Breaks the agreement rule. ", t)
    t = re.sub(r"\s+([,.;:])", r"\1", t)
    t = re.sub(r",\s*,+", ",", t)
    t = re.sub(r",\s*\)", ")", t)
    t = re.sub(r"\(\s*,\s*", "(", t)
    t = re.sub(r"\(\s*\)", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,;")
    # the first sentence, or two when the first is a fragment
    parts = re.split(r"(?<=[.;])\s+(?=[A-Z`])", t)
    s = parts[0].rstrip(",;: ")
    if len(s) < 40 and len(parts) > 1:
        s = s + " " + parts[1].rstrip(",;: ")
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(",;:") + " ..."
    return s.rstrip()


def mono_inline(text: str) -> str:
    """Escape a description, setting `backquoted` spans in monospace."""
    out = []
    for i, piece in enumerate(str(text).split("`")):
        out.append(code(piece) if i % 2 else esc(piece))
    return "".join(out)


# --------------------------------------------------------------------------- ast helpers

def parse(root: str, rel: str) -> ast.Module:
    with open(os.path.join(root, rel), encoding="utf-8") as fh:
        return ast.parse(fh.read(), rel)


def module_constants(root: str, rel: str) -> dict:
    """Top-level NAME = literal assignments of a module."""
    out = {}
    try:
        tree = parse(root, rel)
    except (OSError, SyntaxError):
        return out
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return out


def import_map(tree: ast.Module) -> dict:
    """alias -> module file, for `from pkg import mod as alias` lines."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            base = node.module.replace(".", "/")
            for a in node.names:
                out[a.asname or a.name] = base + "/" + a.name + ".py"
    return out


def render_default(node, ctx: dict) -> str:
    """A readable default for an argparse default expression."""
    if node is None:
        return ""
    if isinstance(node, ast.Constant):
        v = node.value
        if v is None or v == "":
            return "none"
        if isinstance(v, bool):
            return "on" if v else "off"
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        if isinstance(v, int):
            return str(v)
        return str(v)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and node.values:
        # `os.environ.get("X", "1") or 1`: the variable's reading; the fallback is its default
        return render_default(node.values[0], ctx)
    if isinstance(node, ast.Call):
        fn = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
        if fn in ("int", "float", "str") and node.args:
            return render_default(node.args[0], ctx)
        if fn.endswith(".get") and node.args:
            first = node.args[0]
            name = first.value if isinstance(first, ast.Constant) else render_default(first, ctx)
            if fn in ("_S.get", "SETTINGS.get"):
                d = ctx.get("knobs", {}).get(name, (None,))[0]
                return f"{d if d not in (None, '') else 'none'} (QWEN38_{name})"
            if len(node.args) > 1:
                return f"{render_default(node.args[1], ctx)} ({name})"
            return f"none ({name})"
    if isinstance(node, ast.Name) and ctx.get("self"):
        consts = module_constants(ctx["root"], ctx["self"])
        if node.id in consts:
            return render_default(ast.Constant(consts[node.id]), ctx)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        rel = ctx.get("imports", {}).get(node.value.id)
        if rel:
            consts = module_constants(ctx["root"], rel)
            if node.attr in consts:
                return render_default(ast.Constant(consts[node.attr]), ctx)
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover
        return "?"


def kw(call: ast.Call, name: str):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def lit(node, default=None):
    if node is None:
        return default
    try:
        return ast.literal_eval(node)
    except ValueError:
        return default


def help_text(node) -> str:
    if node is None:
        return ""
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{...}" for v in node.values)
    if isinstance(node, ast.BinOp):
        return help_text(node.left) + help_text(node.right)
    return ""


def argparse_rows(root: str, rel: str, func: str = "parser", ctx_extra=None) -> list:
    """Every add_argument call inside `func` (or the whole module when func is None)."""
    tree = parse(root, rel)
    ctx = {"root": root, "imports": import_map(tree), "self": rel}
    ctx.update(ctx_extra or {})
    body = tree
    if func:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func:
                body = node
                break
    rows = []
    for node in ast.walk(body):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument" and node.args):
            continue
        names = [lit(a) for a in node.args if isinstance(lit(a), str)]
        if not names:
            continue
        action = kw(node, "action")
        action_s = ast.unparse(action) if action is not None else ""
        default = kw(node, "default")
        dest = lit(kw(node, "dest"))
        if "store_true" in action_s:
            d = "off (a switch)"
            if default is not None and lit(default) is True:
                d = "on"
        elif "store_false" in action_s:
            d = f"sets {dest or names[0]} off"
        elif "BooleanOptional" in action_s:
            d = render_default(default, ctx)
            d += (f" (--no-{names[0].lstrip('-')} turns it off)" if d == "on"
                  else f" ({names[0]} turns it on)")
        else:
            d = render_default(default, ctx)
            if kw(node, "required") is not None and lit(kw(node, "required")):
                d = "required"
        choices = lit(kw(node, "choices"))
        if choices is None and kw(node, "choices") is not None:
            choices = ast.unparse(kw(node, "choices"))
        rows.append({"flag": ", ".join(names), "default": d,
                     "choices": ", ".join(str(c) for c in choices if c != "") if isinstance(choices, (list, tuple)) else (choices or ""),
                     "help": help_text(kw(node, "help")), "line": node.lineno})
    rows.sort(key=lambda r: r["line"])
    return rows


# Descriptions for flags whose help text is missing or only makes sense in the code's history.
FLAG_NOTES = {
    "--model": "the checkpoint directory of the target model",
    "--drafter": "the drafter set. lenrouter, the served one, holds both DFlash2 widths beside the lookup drafter; none decodes one token a step",
    "--served-model": "the raw engine's model name; with the conductor on it is a hidden alias of TandemLLM",
    "--host": "the address to bind",
    "--port": "the port to listen on",
    "--top-p": "server default for top_p",
    "--top-k": "server default for top_k (0 is off)",
    "--df2-temp": "the block drafter's temperature for its own proposals",
    "--corpus": "the lookup drafter's corpus: a directory of token ids with a suffix array (tools/build_corpus.py)",
    "--depth": "the drafter's depth for the older chain drafters",
    "--draft-head": "an optional separate head file for the drafter",
    "--nvfp4": "the NVFP4 weight files to load, one per target (mlp, gdn, attn); empty means the checkpoint's FP8",
    "--dflash2-path": "how the block drafter walks its lattice: the greedy path or Viterbi",
    "--dflash2-ckpt": "the narrow (block 8) DFlash2 drafter checkpoint",
    "--learn-cost": "the length router learns its verify and draft costs from the blocks it times, across requests (default). --no-learn-cost keeps the served price table; timings inside a request still size its blocks, so two runs can part at a near tie",
    "--relax-tau": "Breaks the agreement rule: accept a drafted token whose probability is at least this fraction of the argmax's. 1.0, the default, keeps the rule",
    "--relax-rank": "Breaks the agreement rule: accept a drafted token among the target's top r. 1, the default, keeps the rule",
    "--response-cache-mb": "the response cache's budget in MiB",
    "--response-cache-ttl": "seconds a response cache entry is kept",
    "--verbose": "more log lines",
    "--label": "the backend's label in the dashboard",
    "--usage-retention-days": "days the usage ledger keeps its rows (at least 400)",
    "--port (launch)": "the engine's port",
}


def served_flags(root: str) -> dict:
    """flag -> the value ops/start.sh passes for the served profile (built from ops/serve.env)."""
    env = dict(read_serve_env(root))
    path = os.path.join(root, "ops", "start.sh")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        src = fh.read()

    def val(var: str, default: str = "") -> str:
        v = env.get(var, default)
        return v

    out = {}
    m = re.search(r'server/app\.py \\\n(.*?)>"\$LOG"', src, re.S)
    cmd = m.group(1) if m else ""
    for flag, var, dflt in re.findall(r'(--[a-z0-9-]+)\s+"?\$\{?([A-Z_0-9]+)(?::-([^}"]*))?\}?"?', cmd):
        out[flag] = val(var, dflt) or dflt
    for flag, value in re.findall(r'(--[a-z0-9-]+)\s+([a-z][a-z0-9_.-]*)(?=\s)', cmd):
        out.setdefault(flag, value)
    for flag in re.findall(r'(--[a-z0-9-]+)\s+(?=\$|\\|--|>)', cmd):
        out.setdefault(flag, "on")
    for var, dflt, want, flag, value in re.findall(
            r'\[ "\$\{(\w+):-([^}]*)\}" = "([^"]*)" \] && \w+="[^"]*?(--[a-z0-9-]+)(?:[ =]([^"$ ]+))?"', src):
        if val(var, dflt) == want:
            out[flag] = value or "on"
    for var, flag in re.findall(r'\[ -n "\$\{(\w+):-\}" \] && \w+="[^"]*?(--[a-z0-9-]+) \$\w+"', src):
        if env.get(var):
            out[flag] = env[var]
    for var, flag in re.findall(r'(\b[A-Z_]+):(--[a-z0-9-]+)', src):
        if env.get(var):
            out[flag] = env[var]
    m = re.search(r'CONDUCTOR_FLAGS="--conductor \$\{CONDUCTOR:-(\w+)\}"', src)
    if m:
        out["--conductor"] = env.get("CONDUCTOR", m.group(1))
    case = re.search(r'case "\$\{SAMPLED_TREE:-\}" in(.*?)esac', src, re.S)
    if case and env.get("SAMPLED_TREE"):
        out["--sampled-tree"] = "det" if env["SAMPLED_TREE"] in ("1", "det") else env["SAMPLED_TREE"]
    for k, v in list(out.items()):
        if "/" in v or "$" in v or "~" in v:
            out[k] = "(a path)"
    return out


def same_value(a: str, b: str) -> bool:
    try:
        return float(a) == float(b)
    except ValueError:
        return a == b


def flags_table(rows: list, caption: str, notes: dict = None, served: dict = None) -> str:
    notes = dict(FLAG_NOTES, **(notes or {}))
    out = []
    for r in rows:
        first = r["flag"].split(", ")[0]
        desc = notes.get(first) or clean(r["help"]) or ""
        extra = f" One of: {r['choices']}." if r["choices"] else ""
        d = r["default"]
        sv = (served or {}).get(first)
        if sv is not None and not same_value(sv, d.split(" (")[0]):
            d = d + "; served: " + sv
        out.append([code(r["flag"]), code(d), mono_inline(desc)
                    + (("" if not desc or desc.endswith(".") else ".") if desc else "") + esc(extra)])
    return table(caption, ["Flag", "Default", "What it does"], out)


# --------------------------------------------------------------------------- the tables

def gen_server_flags(root: str, knobs: dict) -> str:
    rows = argparse_rows(root, "server/app.py", "parser", {"knobs": knobs})
    rows += argparse_rows(root, "server/stage.py", "add_flags", {"knobs": knobs})      # the stage flags
    return flags_table(rows, "Every flag of the engine's server, server/app.py, with the value the served profile passes where it differs",
                       served=served_flags(root))


def gen_install_flags(root: str) -> str:
    with open(os.path.join(root, "install.sh"), encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"usage\(\) \{\s*cat <<EOF\n(.*?)\nEOF", src, re.S)
    text = m.group(1) if m else ""
    defaults = dict(re.findall(r'^([A-Z_]+)="\$\{[A-Z_]+:-([^}]*)\}"', src, re.M))
    rows, cur = [], None
    for line in text.splitlines():
        mm = re.match(r"^  (-\S.*?)\s{2,}(.*)$", line)
        if mm:
            cur = [mm.group(1).strip(), mm.group(2).strip()]
            rows.append(cur)
        elif cur and line.startswith("    ") and line.strip():
            cur[1] += " " + line.strip()
    env = re.search(r"^Environment: (.*)$", text, re.M)
    cells = []
    for flag, desc in rows:
        desc = desc.replace("$TANDEM_REF", defaults.get("TANDEM_REF", "the pinned release"))
        desc = desc.replace("$TANDEM_REPO", "the public repository")
        cells.append([code(flag), esc(desc)])
    out = table("install.sh options", ["Option", "What it does"], cells)
    if env:
        out += ("\nEnvironment variables the installer reads: "
                + ", ".join(code(v.strip()) for v in env.group(1).rstrip(".").split(",")) + ".\n")
    return out


def read_knobs(root: str) -> dict:
    """engine/settings.py's KNOBS and every family's own (engine/models/<pkg>/knobs.py, ENG-405)."""
    def table(rel: str) -> dict:
        for node in parse(root, rel).body:
            if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "KNOBS":
                return ast.literal_eval(node.value)
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "KNOBS":
                return ast.literal_eval(node.value)
        return {}

    knobs = table("engine/settings.py")
    for path in sorted(glob.glob(os.path.join(root, "engine", "models", "*", "knobs.py"))):
        knobs.update(table(os.path.relpath(path, root)))
    return knobs


def read_serve_env(root: str) -> list:
    """(key, value) lines of ops/serve.env; values that are paths are not shown."""
    out = []
    path = os.path.join(root, "ops", "serve.env")
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line.strip())
            if not m:
                continue
            k, v = m.group(1), m.group(2).strip().strip('"').strip("'")
            if "/" in v or "$" in v or "~" in v:
                v = "(a path)"
            out.append((k, v))
    return out


EXT_KNOBS = os.path.join("contracts", "engine-ext-v1", "knobs.json")


def ext_knobs(root: str) -> set:
    """The QWEN38_* names the engine extension reads (engine-ext-v1 knobs.json): TandemApp's rows."""
    with open(os.path.join(root, EXT_KNOBS), encoding="utf-8") as fh:
        return set(json.load(fh)["knobs"])


def gen_engine_knobs(root: str, knobs: dict, served: dict) -> str:
    rows = []
    ext = ext_knobs(root)
    for name in sorted(knobs):
        if name in ext:
            continue
        default, mod = knobs[name][0], knobs[name][1]
        d = "none" if default in (None, "") else str(default)
        sv = served.get("QWEN38_" + name, "")
        rows.append([code("QWEN38_" + name), code(d), (code(sv) + " · " if sv else "") + code(mod)])
    return table("Every QWEN38_* switch the engine reads, with the value ops/serve.env sets (the extension's, in contracts/engine-ext-v1/knobs.json, are in TandemApp's docs)",
                 ["Variable", "Code default", "Served value · read in"], rows)


def gen_serve_env(root: str, knobs: dict) -> str:
    pairs = read_serve_env(root)
    rows = []
    for k, v in pairs:
        if k.startswith("QWEN38_"):
            kind = "engine switch"
        elif k.startswith("QSE_"):
            kind = "server setting"
        elif k.startswith("TANDEM_"):
            kind = "proxy setting"
        else:
            kind = "read by ops/start.sh"
        rows.append([code(k), code(v if v else "(empty)"), esc(kind)])
    return table("The keys ops/serve.env sets, in file order (paths not shown)", ["Key", "Value", "Kind"], rows)


# The code the variable tables scan: the engine's own (ENG-413: TandemApp's code is in TandemApp's
# docs)
QSE_DIRS = ("server", "ops", "install.sh", "engine")
TANDEM_DIRS = ("server", "ops", "install.sh")


def scan_vars(root: str, prefix: str, dirs: tuple) -> dict:
    """Every PREFIX_NAME the code mentions, with the files that mention it."""
    pat = re.compile(r"\b" + prefix + r"_[A-Z0-9_<>]*[A-Z0-9>]\b")
    found: dict = {}
    for d in dirs:
        base = os.path.join(root, d)
        paths = []
        if os.path.isfile(base):
            paths = [base]
        else:
            for dp, dn, fn in os.walk(base):
                dn[:] = [x for x in dn if x not in ("node_modules", "dist", "__pycache__", "tests", "e2e", "mock")]
                paths += [os.path.join(dp, f) for f in fn if f.endswith((".py", ".sh", ".env", ".example", ".ts", ".service"))]
        for p in paths:
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            for name in set(pat.findall(text)):
                found.setdefault(name, set()).add(os.path.relpath(p, root))
    return found


def gen_qse_vars(root: str) -> str:
    found = scan_vars(root, "QSE", QSE_DIRS)
    rows = []
    for name in sorted(found):
        files = sorted(found[name])
        shown = code(", ".join(files[:3])) + (f" and {len(files) - 3} more" if len(files) > 3 else "")
        rows.append([code(name), shown])
    return table("QSE_* variables: the server's settings from the environment or serve.env", ["Variable", "Read in"], rows)


def gen_tandem_vars(root: str) -> str:
    """TANDEM_* variables the engine's code reads (TandemApp's own list is in its docs)."""
    found = scan_vars(root, "TANDEM", TANDEM_DIRS)
    rows = []
    for name in sorted(found):
        if "<" in name:
            continue
        files = sorted(found[name])
        rows.append([code(name), "read in " + code(", ".join(files[:2]))])
    return table("TANDEM_* variables the engine's code reads: the installer, the service scripts and the server",
                 ["Variable", "Read in"], rows)


def gen_metrics(root: str) -> str:
    # the families the contract marks "owner": "ext" are the extension's: TandemApp's docs has them
    with open(os.path.join(root, "contracts", "engine-api-v1", "metrics-families.json"), encoding="utf-8") as fh:
        owner = {f["name"]: f["owner"] for f in json.load(fh)["families"]}
    rows = []
    for rel in ("server/api/metrics.py",):
        try:
            tree = parse(root, rel)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if fn not in ("Counter", "Gauge", "Histogram"):
                continue
            name = lit(node.args[0])
            if not isinstance(name, str) or not name.startswith(("qse_", "tandem_")):
                continue
            if owner.get(name) == "ext":
                continue
            helps = help_text(node.args[1]) if len(node.args) > 1 else ""
            labels = lit(node.args[2]) if len(node.args) > 2 else lit(kw(node, "labels"), ())
            if not isinstance(labels, (list, tuple)):
                labels = ()
            rows.append((name, fn.lower(), ", ".join(labels), helps, rel))
    seen, uniq = set(), []
    for r in rows:
        if r[0] not in seen:
            seen.add(r[0])
            uniq.append(r)
    rows = []
    for name, kind, labels, helps, rel in uniq:
        lab = kind + ((", " + labels) if labels else "")
        rows.append([code(name), code(lab), mono_inline(clean(helps, 200))])
    return table("Prometheus families the engine registers (the extension's are in TandemApp's docs)",
                 ["Metric", "Type, labels", "What it counts"], rows)


LEDGER_NOTES = {
    "ts_ms": "when the request arrived, ms since the epoch",
    "request_id": "the response id",
    "model": "the model name the client sent",
    "client_id": "k: and 12 hex digits of the bearer token's sha256, or anon",
    "client_kind": "read from the User-Agent: open-webui, openai-sdk, opencode, hermes, curl, dashboard, other",
    "endpoint": "the route",
    "stream": "1 for a streamed request",
    "status": "the HTTP status",
    "finish_reason": "stop, length, tool_calls, timeout, error, abandoned",
    "prompt_tokens": "prompt tokens as the client sent them",
    "cached_tokens": "prompt tokens served from a cache",
    "completion_tokens": "tokens streamed to the client",
    "reasoning_tokens": "visible reasoning tokens",
    "queue_ms": "time waiting for the engine",
    "prompt_ms": "prefill time",
    "ttft_ms": "time to the first token",
    "decode_ms": "time after the first token",
    "total_ms": "arrival to the end",
    "decode_tps": "(completion tokens - 1) / decode time; null for a replay",
    "prefill_tps": "forwarded prompt tokens a second",
    "blocks": "verify rounds",
    "draft_tokens": "tokens the drafters proposed",
    "draft_accepted": "drafted tokens the model agreed with",
    "tool_calls": "tool calls in the answer",
    "thinking": "1 when the model thought",
    "cache_source": "where the prompt resumed: resident, end, store, disk, spill, continuation, prepared, response, none",
    "max_tokens": "the output limit",
    "error_type": "the exception class or the refusal",
    "engine_version": "the VERSION file",
    "code_sha": "a hash over the code folders",
    "model_served": "TandemLLM with the conductor on",
    "profile": "the conductor profile", "profile_version": "its version",
    "conductor_mode": "raw, shadow or on",
    "conductor_acts": "the session monitor's executed acts",
    "conductor_hidden_tokens": "tokens the model saw that the client did not send",
    "conductor_acted": "1 when the conductor changed what the model saw or wrote",
    "lane": "decode, prefill or self for a side-lane request",
    "backend": "the backend id of a relayed request", "backend_kind": "its kind",
    "backend_model": "the id sent upstream", "public_model": "the id the client sent",
    "usage_source": "backend or none",
    "cache_write_tokens": "tokens a provider charged as a cache write",
    "cost_micros": "cost in micro-dollars at the model's price",
    "proxy_ms": "the proxy's own time", "retries": "tries again before the first byte",
    "fallback_from": "the backend tried first", "upstream_status": "the backend's HTTP status",
    "user": "the label of the proxy key",
    "preemptions": "preemptions (vLLM) or retractions (SGLang), from the plugin",
    "engine_finish": "the backend engine's own finish reason", "corrupted": "vLLM's NaN check",
    "l2_join": "exact or approximate join of the plugin's numbers",
    "wait_own_ms": "ms the request waited behind the engine's own work (the shadow reader, a summary written ahead, the conductor's question)",
    "parent_request": "the turn an internal row served (the router's question names the turn it routes)",
    "slot": "the engine's sequence slot with --parallel N (0 the main slot, 1 and up a side slot); null without",
    "engine_cached": "the prompt tokens the engine read from a cache, the hidden notes included (cached_tokens counts the client's among them)",
    "forwarded_tokens": "what the prefill forwarded, hidden tokens included",
    "wait_mem_ms": "ms the parallel memory admission held the request (inside queue_ms)",
    "sampling": "how the request sampled: null greedy, client (its own temperature), regenerate (TandemLLM sampled a regenerate)",
}


def gen_ledger(root: str) -> str:
    consts = {}
    tree = parse(root, "server/api/ledger.py")
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            try:
                consts[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    ddl = consts.get("DDL", "")
    base = re.search(r"CREATE TABLE IF NOT EXISTS requests \((.*?)\);", ddl, re.S)
    cols = []
    if base:
        for part in re.split(r",\s*(?![^()]*\))", base.group(1)):
            bits = part.split()
            if len(bits) >= 2 and bits[0] != "id":
                cols.append((bits[0], bits[1], "1"))
    version_of = {}
    v = 2
    for name, kind in consts.get("ADDED", ()):
        if name == "lane":
            v = 2
        if name == "backend":
            v = 3
        if name == "preemptions":
            v = 4
        if name == "wait_own_ms":
            v = 5
        if name == "parent_request":
            v = 6
        if name == "slot":
            v = 7
        if name == "engine_cached":
            v = 8
        if name == "sampling":
            v = 9
        version_of[name] = str(v)
        cols.append((name, kind, str(v)))
    lines = [table("The usage ledger's requests table (schema version " + str(consts.get("SCHEMA_VERSION", "?")) + ")",
                   ["Column", "Type, since", "What it holds"],
                   [[code(name), code(f"{kind}, v{since}"), esc(LEDGER_NOTES.get(name, ""))] for name, kind, since in cols])]
    cdl = consts.get("CONDUCTOR_DDL", "")
    ev = re.search(r"CREATE TABLE IF NOT EXISTS conductor_events \((.*?)\);", cdl, re.S)
    if ev:
        names = [p.split()[0] for p in re.split(r",\s*", ev.group(1)) if p.split()]
        lines.append("The conductor's journal shares the file: `conductor_events` has " + str(len(names))
                     + " columns (" + ", ".join(code(n) for n in names)
                     + "), and `conductor_texts` keeps every hidden text once, by its hash.\n")
    return "\n".join(lines) + "\n"


def _stair(root: str) -> dict:
    with open(os.path.join(root, "ops", "stair-tables-nvfp4.json"), encoding="utf-8") as fh:
        return json.load(fh)


def gen_verify_by_context(root: str) -> str:
    """The 16- and 32-row tree and chain verify by context length, from the served price table."""
    d = _stair(root)
    rows = [[f"{int(c):,}", f"{d[c]['tree']['16']:.2f}", f"{d[c]['chain']['16']:.2f}",
             f"{d[c]['tree']['32']:.2f}", f"{d[c]['chain']['32']:.2f}"] for c in sorted(d, key=int)]
    return table("Verify plus commit by context length, ms (ops/stair-tables-nvfp4.json, measured, published NVFP4 set)",
                 ["context", "tree 16 rows", "chain 16 rows", "tree 32 rows", "chain 32 rows"], rows)


def gen_verify_cost(root: str) -> str:
    """Every row count of the served price table, tree and chain, at each context length."""
    d = _stair(root)
    ctxs = sorted(d, key=int)
    rows_all = sorted({int(r) for c in ctxs for r in d[c]["tree"]})
    heads = ["rows"] + [f"{kind} {int(c) // 1024}k" for c in ctxs for kind in ("tree", "chain")]
    rows = []
    for r in rows_all:
        vals = [str(r)]
        for c in ctxs:
            for kind in ("tree", "chain"):
                v = d[c][kind].get(str(r))
                vals.append(f"{v:.2f}" if v is not None else "")
        rows.append(vals)
    return table("Verify plus commit by rows in the tree, ms (ops/stair-tables-nvfp4.json, measured, published NVFP4 set)",
                 heads, rows)


# --------------------------------------------------------------------------- main

def build(root: str) -> dict:
    """{block name: Markdown} for every generated block."""
    sys.path.insert(0, HERE)
    import http_ref
    knobs = read_knobs(root)
    served = dict(read_serve_env(root))
    return {
        "server-flags": gen_server_flags(root, knobs),
        "install-flags": gen_install_flags(root),
        "engine-knobs": gen_engine_knobs(root, knobs, served),
        "serve-env": gen_serve_env(root, knobs),
        "qse-vars": gen_qse_vars(root),
        "tandem-vars": gen_tandem_vars(root),
        "metrics": gen_metrics(root),
        "ledger": gen_ledger(root),
        "http-who": http_ref.gen_who(table, code, esc),
        "http-engine-routes": http_ref.gen_engine_routes(table, code, esc),
        "http-headers": http_ref.gen_headers(table, code, esc),
        "http-errors": http_ref.gen_errors(table, code, esc, root),
        "verify-cost": gen_verify_cost(root),
        "verify-by-context": gen_verify_by_context(root),
    }


_BLOCK = re.compile(r"(<!-- generated: ([a-z0-9-]+) by tools/gen_reference\.py; do not edit by hand -->\n)(.*?)(<!-- /generated -->)", re.S)


def render(text: str, blocks: dict, used: set) -> str:
    def sub(m: re.Match) -> str:
        name = m.group(2)
        if name not in blocks:
            raise SystemExit(f"unknown generated block: {name}")
        used.add(name)
        return m.group(1) + "\n" + blocks[name] + "\n" + m.group(4)
    return _BLOCK.sub(sub, text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=REPO, help="the checkout to read")
    ap.add_argument("--check", action="store_true", help="exit 1 when a committed block is stale")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    sys.path.insert(0, HERE)
    import http_ref
    blocks = build(root)
    # the HTTP reference is written by hand; the code decides whether it is complete
    gaps = http_ref.problems(root)
    for g in gaps:
        print(f"http reference: {g} (tools/http_ref.py)")
    stale, used = [], set()
    for path in sorted(glob.glob(os.path.join(root, "docs", "**", "*.md"), recursive=True)):
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
        if "<!-- generated: " not in old:
            continue
        new = render(old, blocks, used)
        if new != old:
            stale.append(os.path.relpath(path, root))
            if not a.check:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new)
    missing = sorted(set(blocks) - used)
    for m in missing:
        print(f"no page has the block {m} (docs/**/*.md)")
    if a.check:
        for s_ in stale:
            print(f"stale: {s_}")
        return 1 if stale or gaps or missing else 0
    print(f"rewrote {len(stale)} page(s)" if stale else "every page up to date")
    return 2 if gaps or missing else 0


if __name__ == "__main__":
    sys.exit(main())
