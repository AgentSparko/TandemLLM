"""A link between two processes on one box: a TCP relay with a set delay and rate, and a control
socket that cuts, stalls and heals it (OPS-197). Standard library only.

    python3 tools/linkrelay.py --listen 127.0.0.1:9101 --to 127.0.0.1:9100 \\
            [--delay-us 50] [--rate-mbit 10000] [--control PATH.sock] [--log PATH.jsonl]
    python3 tools/linkrelay.py ctl PATH.sock '{"cmd": "stats"}'

Both directions are relayed. Each stream direction is a FIFO whose chunks are released at arrival
+ delay; each direction (all streams together, as one link) is paced by a token bucket of
--rate-mbit (0: no limit). TCP_NODELAY on every socket. Addresses are IP literals.

The control socket takes one JSON line a command and answers one JSON line:
    {"cmd": "cut"}        close every stream and refuse new ones until heal
    {"cmd": "blackhole"}  keep streams open and forward nothing (a stall: nothing is dropped)
    {"cmd": "heal"}       end a cut or a blackhole
    {"cmd": "set", "delay_us": N, "rate_mbit": N}   either key or both
    {"cmd": "stats"}      bytes each way, connects, and the relay's own added latency

`own_us` is the relay's own cost per chunk (receive to send done, less the delay and pacing it
was told to add), p50 and p99 over the last 10,000 chunks. Measured at delay 0 it is the floor of
this relay on loopback, not a link cost. On SIGTERM or SIGINT the relay prints one JSON stats line
and exits 0.
"""

from __future__ import annotations

import argparse
import collections
import ipaddress
import json
import os
import signal
import socket
import sys
import threading
import time

CHUNK = 65536
OWN_KEEP = 10000


def _addr(text: str) -> tuple[str, int]:
    host, _, port = text.rpartition(":")
    host = host.strip("[]")
    ipaddress.ip_address(host)                 # an IP literal, never a name
    return host, int(port)


def _pct(xs: list, q: float):
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(q * len(s)))], 1)


class Pacer:
    """The token bucket of one direction, as a virtual clock: n bytes may start once the bytes before
    them have drained at the rate. A burst of at most `burst_s` of idle time is credited."""

    def __init__(self):
        self.lock = threading.Lock()
        self.t = 0.0

    def wait_for(self, n: int, rate_bps: float, burst_s: float = 0.002) -> float:
        if rate_bps <= 0:
            return 0.0
        with self.lock:
            now = time.monotonic()
            start = max(self.t, now - burst_s)
            self.t = start + n / rate_bps
        return max(0.0, start - now)


class Relay:
    def __init__(self, to: tuple[str, int], delay_us: float, rate_mbit: float, log: str | None):
        self.to = to
        self.delay_us = float(delay_us)
        self.rate_mbit = float(rate_mbit)
        self.state = "ok"                       # ok | cut | blackhole
        self.cond = threading.Condition()
        self.streams: set = set()
        self.bytes = {"up": 0, "down": 0}
        self.connects = 0
        self.refused = 0
        self.own: collections.deque = collections.deque(maxlen=OWN_KEEP)
        self.pacers = {"up": Pacer(), "down": Pacer()}
        self.stopping = False
        self.log_path = log
        self.log_lock = threading.Lock()

    def log(self, event: str, **kw) -> None:
        if not self.log_path:
            return
        row = {"t": round(time.time(), 6), "event": event, **kw}
        with self.log_lock, open(self.log_path, "a") as f:
            f.write(json.dumps(row) + "\n")

    def stats(self) -> dict:
        own = list(self.own)
        with self.cond:
            return {"state": self.state, "delay_us": self.delay_us, "rate_mbit": self.rate_mbit,
                    "up_bytes": self.bytes["up"], "down_bytes": self.bytes["down"],
                    "connects": self.connects, "refused": self.refused, "open": len(self.streams),
                    "own_us": {"p50": _pct(own, 0.50), "p99": _pct(own, 0.99), "n": len(own)}}

    # ------------------------------------------------------------------ control
    def command(self, msg: dict) -> dict:
        cmd = msg.get("cmd")
        if cmd == "stats":
            return {"ok": True, **self.stats()}
        if cmd == "set":
            with self.cond:
                if "delay_us" in msg:
                    self.delay_us = max(0.0, float(msg["delay_us"]))
                if "rate_mbit" in msg:
                    self.rate_mbit = max(0.0, float(msg["rate_mbit"]))
                self.cond.notify_all()
        elif cmd == "cut":
            with self.cond:
                self.state = "cut"
                doomed = list(self.streams)
                self.cond.notify_all()
            for s in doomed:
                s.close()
        elif cmd == "blackhole":
            with self.cond:
                if self.state != "cut":
                    self.state = "blackhole"
                self.cond.notify_all()
        elif cmd == "heal":
            with self.cond:
                self.state = "ok"
                self.cond.notify_all()
        else:
            return {"ok": False, "error": f"unknown cmd {cmd!r}"}
        self.log("cmd", **msg)
        with self.cond:
            return {"ok": True, "state": self.state, "delay_us": self.delay_us,
                    "rate_mbit": self.rate_mbit}

    def serve_control(self, path: str) -> None:
        if os.path.exists(path):
            os.unlink(path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        os.chmod(path, 0o600)
        srv.listen(8)
        while not self.stopping:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=self._control_client, args=(c,), daemon=True).start()

    def _control_client(self, c: socket.socket) -> None:
        with c, c.makefile("rwb") as f:
            for line in f:
                try:
                    reply = self.command(json.loads(line))
                except (ValueError, TypeError) as exc:
                    reply = {"ok": False, "error": str(exc)}
                f.write((json.dumps(reply) + "\n").encode())
                f.flush()

    # ------------------------------------------------------------------ streams
    def accept(self, client: socket.socket) -> None:
        with self.cond:
            cut = self.state == "cut"
            self.refused += cut
        if cut:
            client.close()
            return
        try:
            up = socket.create_connection(self.to, timeout=5)
        except OSError as exc:
            self.log("upstream_error", error=str(exc))
            client.close()
            return
        up.settimeout(None)
        for s in (client, up):
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        st = Stream(self, client, up)
        with self.cond:
            self.connects += 1
            self.streams.add(st)
        self.log("connect", n=self.connects)
        st.start()


class Stream:
    """One relayed connection: per direction a reader that timestamps chunks into a FIFO and a writer
    that releases them at arrival + delay through the direction's pacer."""

    def __init__(self, relay: Relay, client: socket.socket, up: socket.socket):
        self.r, self.client, self.up = relay, client, up
        self.closed = False
        self.done = 0
        self.lock = threading.Lock()

    def start(self) -> None:
        for d, src, dst in (("up", self.client, self.up), ("down", self.up, self.client)):
            q: collections.deque = collections.deque()
            threading.Thread(target=self._read, args=(d, src, q), daemon=True).start()
            threading.Thread(target=self._write, args=(d, dst, q), daemon=True).start()

    def _held(self) -> bool:
        return self.r.state == "blackhole" and not self.closed

    def _read(self, d: str, src: socket.socket, q) -> None:
        r = self.r
        while not self.closed:
            with r.cond:
                while self._held():
                    r.cond.wait(0.05)
            try:
                data = src.recv(CHUNK)
            except OSError:
                data = b""
            with r.cond:
                q.append((time.monotonic(), data))
                r.cond.notify_all()
            if not data:
                return

    def _write(self, d: str, dst: socket.socket, q) -> None:
        r = self.r
        while True:
            with r.cond:
                while (not q or self._held()) and not self.closed:
                    r.cond.wait(0.05)
                if self.closed:
                    return
                arrived, data = q.popleft()
                delay_s, rate_bps = r.delay_us / 1e6, r.rate_mbit * 1e6 / 8
            if not data:                                   # the sender's EOF, passed on
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                self._finished()
                return
            planned = arrived + delay_s
            wait = planned - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            pace = r.pacers[d].wait_for(len(data), rate_bps)
            if pace > 0:
                time.sleep(pace)
            try:
                dst.sendall(data)
            except OSError:
                self.close()
                return
            sent = time.monotonic()
            own = (sent - max(arrived, planned) - pace) * 1e6
            with r.cond:
                r.bytes[d] += len(data)
            r.own.append(max(0.0, own))

    def _finished(self) -> None:
        with self.lock:
            self.done += 1
            last = self.done == 2
        if last:
            self.close()

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
        for s in (self.client, self.up):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()
        with self.r.cond:
            self.r.streams.discard(self)
            self.r.cond.notify_all()


def ctl(path: str, line: str) -> int:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
        c.connect(path)
        c.sendall(line.strip().encode() + b"\n")
        f = c.makefile("rb")
        reply = f.readline().decode().strip()
    print(reply)
    return 0 if json.loads(reply).get("ok") else 1


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["ctl"]:
        if len(argv) != 3:
            print("usage: linkrelay.py ctl PATH.sock JSON", file=sys.stderr)
            return 2
        return ctl(argv[1], argv[2])
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--listen", required=True, help="HOST:PORT, an IP literal")
    ap.add_argument("--to", required=True, help="HOST:PORT, an IP literal")
    ap.add_argument("--delay-us", type=float, default=0.0, help="added one-way delay (µs)")
    ap.add_argument("--rate-mbit", type=float, default=0.0, help="each direction's rate; 0: no limit")
    ap.add_argument("--control", default=None, help="the control socket (a path)")
    ap.add_argument("--log", default=None, help="events as JSON lines")
    a = ap.parse_args(argv)
    try:
        listen, to = _addr(a.listen), _addr(a.to)
    except ValueError as exc:
        ap.error(f"addresses are IP:PORT literals ({exc})")
    relay = Relay(to, a.delay_us, a.rate_mbit, a.log)
    fam = socket.AF_INET6 if ":" in listen[0] else socket.AF_INET
    srv = socket.socket(fam, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(listen)
    srv.listen(64)
    srv.settimeout(0.2)

    def on_signal(_sig, _frame):
        relay.stopping = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    if a.control:
        threading.Thread(target=relay.serve_control, args=(a.control,), daemon=True).start()
    print(f"[linkrelay] {listen[0]}:{listen[1]} -> {to[0]}:{to[1]}, delay {a.delay_us:g} us, "
          f"rate {a.rate_mbit:g} Mbit/s{' (no limit)' if not a.rate_mbit else ''}"
          f"{', control ' + a.control if a.control else ''}", flush=True)
    relay.log("start", listen=a.listen, to=a.to, delay_us=a.delay_us, rate_mbit=a.rate_mbit)
    while not relay.stopping:
        try:
            c, _ = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        c.settimeout(None)
        relay.accept(c)
    srv.close()
    for s in list(relay.streams):
        s.close()
    if a.control and os.path.exists(a.control):
        os.unlink(a.control)
    final = relay.stats()
    relay.log("exit", **final)
    print(json.dumps(final), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
