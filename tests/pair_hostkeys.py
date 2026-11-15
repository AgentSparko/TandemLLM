"""Host keys and public keys for the pairing tests: an etc/ssh of a test's own and well-formed
ssh-ed25519 lines. Nothing of the box's /etc/ssh is read."""

from __future__ import annotations

import base64
import hashlib
import os
import struct


def ed25519_line(label: str) -> str:
    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + hashlib.sha256(label.encode()).digest()
    return "ssh-ed25519 " + base64.b64encode(blob).decode()


def host_etc(base: str, kinds=("ed25519",), config: str | None = None) -> str:
    """An etc/ssh of a test's own: ssh-keygen's host keys when it exists, else fixed lines."""
    import shutil
    import subprocess
    etc = os.path.join(base, "etc-ssh")
    os.makedirs(etc, exist_ok=True)
    for k in kinds:
        path = os.path.join(etc, f"ssh_host_{k}_key")
        if shutil.which("ssh-keygen"):
            subprocess.run(["ssh-keygen", "-q", "-t", k, "-N", "", "-C", "", "-f", path], check=True,
                           capture_output=True, stdin=subprocess.DEVNULL)
        else:
            with open(path + ".pub", "w") as fh:
                fh.write(ed25519_line(f"host {k}") + "\n")
    if config is not None:
        with open(os.path.join(etc, "sshd_config"), "w") as fh:
            fh.write(config)
    return etc
