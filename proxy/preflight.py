"""Preflight (VIS-246, the Add Spark wizard's Check step): what a box is and whether TandemNode and
the engine can run there, one row per check, each `ok`, `warn` or `fail` with its value and, for
anything but ok, the fix in words.

    facts = parse(output of SCRIPT)               the box as the script printed it
    rows = checks(facts, fleet_version, now)      [{id, label, state, value, fix}]
    verdict(rows) -> {fails, warns, install}      install: needed | skip | refused, and why

ONE SCRIPT, TWO WAYS. SCRIPT is POSIX sh that reads only world-readable files and asks nothing
(never `sudo` with a password: `sudo -n true` only says whether one is needed). A paired node runs
it through its TandemNode agent (`GET /node/preflight`, proxy/nodeagent.py); a box with nothing
installed runs it in the SSH-once login the wizard makes with the user's password or key
(proxy/sshonce.py). Its lines are `TANDEM-PF key=value`; anything else is ignored.

THE CHECKS (concept §6 Check):

    os        DGX OS (or Ubuntu 24.04+ on aarch64)    another Linux warns; not Linux fails
    kernel    the running kernel                       shown
    driver    the NVIDIA driver                        missing fails
    cuda      the CUDA the driver offers               below 13.0 fails: the engine's torch is cu130
    disk      free space in the home directory         < 20 GiB fails, < 100 GiB warns (weights)
    memory    MemTotal                                 < 64 GiB warns
    tandemllm TandemLLM on the box                     not installed: Install puts the fleet's;
                                                       the fleet's release: Install is skipped;
                                                       another release fails: Install refuses it
                                                       (a local label, +build, is the same release)
    cx7       ConnectX ports and their link state     none warns (LAN only, no Split)
    sudo      sudo for the Link step                   none warns; asked per action, never stored
    clock     NTP synchronised, and the box's clock    off or > 5 s from the controller's fails
    linger    user services after logout               off warns: the agent stops at logout
    python    Python 3.11+ with venv (Install)         missing fails, only where Install runs
"""

from __future__ import annotations

import re

MIN_CUDA = (13, 0)
DISK_FAIL_GIB, DISK_WARN_GIB = 20, 100
MEM_WARN_GIB = 64
CLOCK_SKEW_S = 5.0
STATES = ("ok", "warn", "fail")

SCRIPT = r"""set -u
pf() { printf 'TANDEM-PF %s=%s\n' "$1" "$(printf '%s' "$2" | tr '\n\r' '  ')"; }
pf now "$(date +%s)"
pf user "$(id -un)"
pf home "$HOME"
pf arch "$(uname -m)"
pf kernel "$(uname -r)"
pf system "$(uname -s)"
if [ -r /etc/os-release ]; then
  pf os "$(. /etc/os-release; printf '%s' "${PRETTY_NAME:-$NAME}")"
  pf os_id "$(. /etc/os-release; printf '%s %s' "${ID:-}" "${VERSION_ID:-}")"
fi
[ -r /etc/dgx-release ] && pf dgx "$(sed -n 's/^DGX_PRETTY_NAME=//p' /etc/dgx-release | tr -d '"' | head -n 1)"
d=/sys/devices/virtual/dmi/id
pf dmi "$(cat $d/sys_vendor 2>/dev/null)|$(cat $d/product_name 2>/dev/null)|$(cat $d/product_family 2>/dev/null)"
[ -r /proc/driver/nvidia/version ] && pf driver "$(grep -o '[0-9]\{3\}\.[0-9][0-9.]*' /proc/driver/nvidia/version | head -n 1)"
if command -v nvidia-smi >/dev/null 2>&1; then
  pf gpu "$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n 1)"
  pf cuda "$(nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: *\([0-9.]*\).*/\1/p' | head -n 1)"
  pf driver_smi "$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n 1)"
fi
pf mem_kb "$(sed -n 's/^MemTotal: *\([0-9]*\).*/\1/p' /proc/meminfo 2>/dev/null)"
pf disk_kb "$(df -Pk "$HOME" 2>/dev/null | awk 'NR==2 {print $4}')"
pf disk_mount "$(df -Pk "$HOME" 2>/dev/null | awk 'NR==2 {print $6}')"
v=""
if [ -n "${TANDEM_PF_SELF:-}" ]; then v="$TANDEM_PF_SELF"
elif [ -n "${TANDEM_PF_PREFIX:-}" ] && [ -x "$TANDEM_PF_PREFIX/venv/bin/python" ]; then
  v=$("$TANDEM_PF_PREFIX/venv/bin/python" -c 'from proxy import _version; print(_version.release())' 2>/dev/null)
fi
if [ -z "$v" ] && command -v tandem-node >/dev/null 2>&1; then
  py=$(head -n 1 "$(command -v tandem-node)" | sed 's/^#!//')
  v=$("$py" -c 'from proxy import _version; print(_version.release())' 2>/dev/null)
fi
[ -z "$v" ] && [ -r "$HOME/TandemLLM/src/VERSION" ] && v=$(cat "$HOME/TandemLLM/src/VERSION")
pf tandemllm "$v"
ports=""
for dev in /sys/class/infiniband/*; do
  [ -d "$dev" ] || continue
  n=$(ls "$dev/device/net" 2>/dev/null | head -n 1)
  st=$(cat "$dev/ports/1/state" 2>/dev/null | sed 's/^[0-9]*: *//')
  rate=$(cat "$dev/ports/1/rate" 2>/dev/null | sed 's/ .*//')
  ports="$ports${ports:+,}${n:-$(basename "$dev")}:${st:-?}:${rate:-?}"
done
pf cx7 "$ports"
if sudo -n true 2>/dev/null; then pf sudo nopasswd
elif id -nG 2>/dev/null | tr ' ' '\n' | grep -qx -e sudo -e wheel -e admin; then pf sudo password
else pf sudo none
fi
if command -v timedatectl >/dev/null 2>&1; then
  pf ntp "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)"
  pf ntp_on "$(timedatectl show -p NTP --value 2>/dev/null)"
fi
command -v loginctl >/dev/null 2>&1 && pf linger "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)"
if command -v python3 >/dev/null 2>&1; then
  pf python "$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"
  python3 -c 'import venv, ensurepip' 2>/dev/null && pf venv yes || pf venv no
fi
command -v systemctl >/dev/null 2>&1 && pf systemd yes || pf systemd no
"""


def same_release(a: str | None, b: str | None) -> bool:
    """One TandemLLM release: `0.15.1-test` (VERSION) and `0.15.1.dev0` (an installed wheel's
    metadata) are one, and a local label (`0.15.0+flashnext-3.05`, a recipe's build of the
    release) is no other release."""
    if not a or not b:
        return False
    from proxy import _version

    def norm(v: str) -> str:
        v = v.strip().split("+", 1)[0]
        try:
            return _version.pep440(v)
        except ValueError:
            return v                         # PEP 440 already (an installed wheel's)
    return norm(a) == norm(b)


def parse(text: str) -> dict:
    """The script's `TANDEM-PF key=value` lines as a dict (the first value of a key wins)."""
    out: dict = {}
    for line in (text or "").splitlines():
        if not line.startswith("TANDEM-PF "):
            continue
        k, sep, v = line[len("TANDEM-PF "):].partition("=")
        if sep and k and k not in out:
            out[k.strip()] = v.strip()
    return out


def _ver(text: str | None) -> tuple[int, ...] | None:
    m = re.match(r"^\s*(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _gib(kb: str | None) -> float | None:
    try:
        return int(kb) / (1024 * 1024)
    except (TypeError, ValueError):
        return None


def row(cid: str, label: str, state: str, value: str, fix: str = "") -> dict:
    return {"id": cid, "label": label, "state": state, "value": value, "fix": fix or None}


def checks(f: dict, fleet_version: str, now: float, *, install: bool = True) -> list[dict]:
    """One row per check. `install`: the box would get TandemNode over SSH here (a bare box),
    so Python and the user's services are checked too."""
    out = []
    linux = (f.get("system") or "Linux") == "Linux"
    os_name = f.get("dgx") or f.get("os") or f.get("system") or "unknown"
    os_id = (f.get("os_id") or "").split()
    ubuntu = len(os_id) == 2 and os_id[0] == "ubuntu" and (_ver(os_id[1]) or (0,)) >= (24, 4)
    arch = f.get("arch") or "?"
    if not linux:
        out.append(row("os", "OS", "fail", f"{os_name} · {arch}", "TandemNode runs on Linux: "
                       "DGX OS on a DGX Spark or another GB10 box"))
    elif f.get("dgx") or (ubuntu and arch == "aarch64"):
        out.append(row("os", "OS", "ok", f"{os_name} · {arch}"))
    else:
        out.append(row("os", "OS", "warn", f"{os_name} · {arch}", "Not DGX OS: TandemLLM is "
                       "built for DGX OS (Ubuntu 24.04, aarch64); this box is best effort"))
    out.append(row("kernel", "Kernel", "ok" if f.get("kernel") else "warn",
                   f.get("kernel") or "not read", "" if f.get("kernel") else
                   "uname -r said nothing"))
    drv = f.get("driver_smi") or f.get("driver")
    if drv:
        out.append(row("driver", "NVIDIA driver", "ok", drv + (f" · {f['gpu']}" if f.get("gpu")
                                                               else "")))
    else:
        out.append(row("driver", "NVIDIA driver", "fail", "none loaded", "Install the NVIDIA "
                       "driver (DGX OS ships it; on another Ubuntu: sudo ubuntu-drivers install), "
                       "then reboot"))
    cuda = _ver(f.get("cuda"))
    if cuda is None:
        out.append(row("cuda", "CUDA", "fail" if not drv else "warn", "not reported",
                       "nvidia-smi names the CUDA version the driver offers; with no driver "
                       "there is none" if not drv else "nvidia-smi did not say its CUDA "
                       "version; the engine needs 13.0"))
    elif cuda < MIN_CUDA:
        out.append(row("cuda", "CUDA", "fail", f.get("cuda"), "The engine's torch is built for "
                       "CUDA 13.0: update the NVIDIA driver to one that offers 13.0 or later"))
    else:
        out.append(row("cuda", "CUDA", "ok", f.get("cuda")))
    disk = _gib(f.get("disk_kb"))
    where = f.get("disk_mount") or f.get("home") or "~"
    if disk is None:
        out.append(row("disk", "Disk free", "warn", "not read", f"df {where} said nothing"))
    elif disk < DISK_FAIL_GIB:
        out.append(row("disk", "Disk free", "fail", f"{disk:.0f} GiB on {where}",
                       f"TandemNode and the engine's environment need {DISK_FAIL_GIB} GiB: free "
                       f"space on {where}"))
    elif disk < DISK_WARN_GIB:
        out.append(row("disk", "Disk free", "warn", f"{disk:.0f} GiB on {where}",
                       f"Enough for TandemNode and the engine; a model's weights need "
                       f"{DISK_WARN_GIB} GiB or more: free space before Serve a model"))
    else:
        out.append(row("disk", "Disk free", "ok", f"{_size(disk)} on {where}"))
    mem = _gib(f.get("mem_kb"))
    if mem is None:
        out.append(row("memory", "Memory", "warn", "not read", "/proc/meminfo said nothing"))
    else:
        out.append(row("memory", "Memory", "ok" if mem >= MEM_WARN_GIB else "warn",
                       f"{mem:.1f} GiB", "" if mem >= MEM_WARN_GIB else
                       "Less than a GB10's 128 GB: only small models fit here"))
    have = (f.get("tandemllm") or "").strip()
    if not have:
        out.append(row("tandemllm", "TandemLLM", "ok", f"not installed · Install puts "
                       f"{fleet_version}"))
    elif same_release(have, fleet_version):
        out.append(row("tandemllm", "TandemLLM", "ok", f"{have} · the fleet's version"))
    else:
        out.append(row("tandemllm", "TandemLLM", "fail", f"{have} · the fleet runs "
                       f"{fleet_version}", f"Every node runs the fleet's version: update "
                       f"TandemLLM on this box to {fleet_version} (bash install.sh --ref "
                       f"v{fleet_version}), then check again. Install refuses {have}"))
    cx = [p.split(":") for p in (f.get("cx7") or "").split(",") if p]
    if not cx:
        out.append(row("cx7", "ConnectX-7", "warn", "no ConnectX port", "LAN only: no fabric "
                       "link, so no Split across this box; Single and Replicas work"))
    else:
        words = []
        for p in cx:
            name, st, rate = (p + ["?", "?"])[:3]
            up = st.upper().startswith("ACTIVE")
            words.append(f"{name} {'up ' + rate + 'G' if up and rate != '?' else 'up' if up else 'no link'}")
        out.append(row("cx7", "ConnectX-7", "ok", f"{len(cx)} port{'s' if len(cx) > 1 else ''} · "
                       + ", ".join(words)))
    sudo = f.get("sudo")
    if sudo in ("nopasswd", "password"):
        out.append(row("sudo", "sudo", "ok", "available · asked per action in the Link step, "
                       "never stored"))
    else:
        out.append(row("sudo", "sudo", "warn", "not available to this user",
                       "Only the Link step needs it (link addresses, MTU): add the user to the "
                       "sudo group, or set the link up by hand"))
    skew = None
    try:
        skew = float(f["now"]) - now
    except (KeyError, TypeError, ValueError):
        pass
    synced = f.get("ntp")
    if skew is not None and abs(skew) > CLOCK_SKEW_S:
        out.append(row("clock", "Clock", "fail", f"{abs(skew):.0f} s {'ahead' if skew > 0 else 'behind'}"
                       f"{' · NTP off' if f.get('ntp_on') == 'no' else ''}",
                       "Run sudo timedatectl set-ntp true on the node. Certificates and metrics "
                       "need a correct clock"))
    elif synced == "no":
        out.append(row("clock", "Clock", "fail", "not synchronised" +
                       (" · NTP off" if f.get("ntp_on") == "no" else ""),
                       "Run sudo timedatectl set-ntp true on the node. Certificates and metrics "
                       "need a correct clock"))
    else:
        out.append(row("clock", "Clock", "ok", "synchronised" if synced == "yes" else
                       "within 5 s of the controller"))
    if install:
        lg = f.get("linger")
        if lg == "no":
            out.append(row("linger", "Services after logout", "warn", "off for " +
                           (f.get("user") or "this user"), f"The agent is a user service and stops "
                           f"when the last session ends: run sudo loginctl enable-linger "
                           f"{f.get('user') or '$USER'} on the node"))
        else:
            out.append(row("linger", "Services after logout", "ok", "kept (linger on)" if lg ==
                           "yes" else "not reported"))
        py = _ver(f.get("python"))
        if py is None or py < (3, 11) or f.get("venv") != "yes":
            out.append(row("python", "Python", "fail", (f.get("python") or "none") +
                           ("" if f.get("venv") == "yes" else " · no venv"),
                           "Install needs Python 3.11 or later with venv: sudo apt install "
                           "python3-venv (DGX OS has Python 3.12)"))
        else:
            out.append(row("python", "Python", "ok", f"{f.get('python')} with venv"))
        if f.get("systemd") != "yes":
            out.append(row("systemd", "systemd", "fail", "not found", "The agent runs as a "
                           "systemd user unit; this box has no systemctl"))
    return out


def _size(gib: float) -> str:
    return f"{gib / 1024:.1f} TiB" if gib >= 1024 else f"{gib:.0f} GiB"


def verdict(rows: list[dict], installed: bool) -> dict:
    """{fails, warns, install: needed|skip|refused, why}. `installed`: TandemNode answers there
    (a paired node): Install is skipped when its TandemLLM is the fleet's."""
    fails = [r for r in rows if r["state"] == "fail"]
    tv = next((r for r in rows if r["id"] == "tandemllm"), None)
    if tv is not None and tv["state"] == "fail":
        install, why = "refused", tv["value"]
    elif installed:
        install, why = "skip", "TandemNode answers there and runs the fleet's version"
    else:
        install, why = "needed", "nothing installed"
    return {"fails": len(fails), "warns": sum(1 for r in rows if r["state"] == "warn"),
            "install": install, "why": why}
