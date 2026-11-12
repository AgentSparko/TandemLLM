"""tools/linkrelay.py, the link of the one-box emulation (OPS-197), on loopback with real sockets:

  1  64 MiB of random bytes arrive through the relay with the same sha256
  2  a round trip through a relay with delay D takes at least 2 D
  3  at a set rate, the bytes that arrive in 2 s are within 15 % of the rate
  4  cut ends a client within 100 ms and refuses new connects; heal lets new connects pass
  5  blackhole passes 0 bytes and keeps the socket open; heal passes new connects and the held bytes
  6  stats count bytes and connects and report the relay's own latency; the exit prints one JSON line

Run: python3 tests/test_linkrelay.py
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELAY = os.path.join(ROOT, "tools", "linkrelay.py")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Upstream:
    """A loopback server: `echo` sends back what it gets, `sha` answers the sha256 of everything
    it got once the client shuts its side, `sink` counts bytes with their arrival times."""

    def __init__(self, mode: str):
        self.mode = mode
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(16)
        self.port = self.srv.getsockname()[1]
        self.got = 0
        self.times: list = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(c,), daemon=True).start()

    def _one(self, c: socket.socket):
        h = hashlib.sha256()
        with c:
            while True:
                try:
                    data = c.recv(65536)
                except OSError:
                    return
                if not data:
                    break
                self.got += len(data)
                if self.mode == "echo":
                    c.sendall(data)
                elif self.mode == "sha":
                    h.update(data)
                else:
                    self.times.append((time.monotonic(), len(data)))
            if self.mode == "sha":
                c.sendall(h.hexdigest().encode())

    def close(self):
        self.srv.close()


class Relay:
    def __init__(self, to: int, *args: str):
        self.dir = tempfile.mkdtemp(prefix="linkrelay-")
        self.port = _free_port()
        self.ctl = os.path.join(self.dir, "ctl.sock")
        self.p = subprocess.Popen([sys.executable, RELAY, "--listen", f"127.0.0.1:{self.port}",
                                   "--to", f"127.0.0.1:{to}", "--control", self.ctl,
                                   "--log", os.path.join(self.dir, "log.jsonl"), *args],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        end = time.monotonic() + 10
        while not os.path.exists(self.ctl):
            assert time.monotonic() < end and self.p.poll() is None, self.p.stderr.read()
            time.sleep(0.02)

    def cmd(self, **msg) -> dict:
        with socket.socket(socket.AF_UNIX) as c:
            c.connect(self.ctl)
            c.sendall(json.dumps(msg).encode() + b"\n")
            return json.loads(c.makefile("rb").readline())

    def connect(self) -> socket.socket:
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return s

    def stop(self) -> dict:
        self.p.send_signal(signal.SIGTERM)
        out, err = self.p.communicate(timeout=10)
        assert self.p.returncode == 0, err
        return json.loads(out.strip().splitlines()[-1])


def test_64_mib_arrive_with_the_same_sha256():
    up = Upstream("sha")
    r = Relay(up.port)
    try:
        blob = os.urandom(64 << 20)
        with r.connect() as s:
            s.settimeout(60)
            s.sendall(blob)
            s.shutdown(socket.SHUT_WR)
            got = b""
            while True:
                d = s.recv(256)
                if not d:
                    break
                got += d
        assert got.decode() == hashlib.sha256(blob).hexdigest()
        assert up.got == len(blob)
    finally:
        st = r.stop()
        up.close()
    assert st["up_bytes"] == 64 << 20 and st["down_bytes"] == 64 and st["connects"] == 1, st


def test_a_round_trip_takes_at_least_the_set_delay_each_way():
    up = Upstream("echo")
    r = Relay(up.port, "--delay-us", "20000")
    try:
        with r.connect() as s:
            for _ in range(5):
                t = time.monotonic()
                s.sendall(b"x" * 100)
                got = b""
                while len(got) < 100:
                    got += s.recv(100)
                rtt = time.monotonic() - t
                assert rtt >= 0.040, rtt
        assert r.cmd(cmd="set", delay_us=0)["delay_us"] == 0
        with r.connect() as s:
            t = time.monotonic()
            s.sendall(b"y")
            assert s.recv(1) == b"y"
            assert time.monotonic() - t < 0.040
    finally:
        r.stop()
        up.close()


def test_the_rate_holds_within_15_percent_over_2_s():
    up = Upstream("sink")
    r = Relay(up.port, "--rate-mbit", "80")              # 10 MB/s
    stop = threading.Event()
    try:
        s = r.connect()

        def pump():
            chunk = b"z" * 65536
            try:
                while not stop.is_set():
                    s.sendall(chunk)
            except OSError:
                pass
        threading.Thread(target=pump, daemon=True).start()
        end = time.monotonic() + 10
        while not up.times:
            assert time.monotonic() < end
            time.sleep(0.01)
        time.sleep(2.3)
        t0 = up.times[0][0]
        n = sum(b for t, b in list(up.times) if t - t0 <= 2.0)
        rate = n / 2.0
        assert abs(rate - 10e6) <= 0.15 * 10e6, rate
    finally:
        stop.set()
        s.close()
        r.stop()
        up.close()


def test_cut_ends_the_client_within_100_ms_and_heal_lets_new_connects_pass():
    up = Upstream("echo")
    r = Relay(up.port)
    try:
        s = r.connect()
        s.sendall(b"a")
        assert s.recv(1) == b"a"
        t = time.monotonic()
        assert r.cmd(cmd="cut")["state"] == "cut"
        s.settimeout(1)
        try:
            ended = s.recv(1) == b""
        except ConnectionResetError:
            ended = True
        assert ended and time.monotonic() - t < 0.100, time.monotonic() - t
        s.close()
        with r.connect() as s2:                          # accepted and closed at once
            s2.settimeout(1)
            try:
                assert s2.recv(1) == b""
            except ConnectionResetError:
                pass
        assert r.cmd(cmd="heal")["state"] == "ok"
        with r.connect() as s3:
            s3.sendall(b"b")
            assert s3.recv(1) == b"b"
        st = r.cmd(cmd="stats")
        assert st["refused"] == 1 and st["connects"] == 2, st
    finally:
        r.stop()
        up.close()


def test_blackhole_passes_nothing_keeps_the_socket_and_heal_resumes():
    up = Upstream("echo")
    r = Relay(up.port)
    try:
        s = r.connect()
        s.sendall(b"a")
        assert s.recv(1) == b"a"
        base = up.got
        assert r.cmd(cmd="blackhole")["state"] == "blackhole"
        s.sendall(b"q" * 1000)
        time.sleep(0.3)
        assert up.got == base, up.got
        s.settimeout(0.3)
        try:
            s.recv(1)
            raise AssertionError("bytes or an end came through a blackhole")
        except socket.timeout:
            pass                                         # open, and silent
        assert r.cmd(cmd="heal")["state"] == "ok"
        s.settimeout(5)
        got = b""
        while len(got) < 1000:
            got += s.recv(2000)
        assert got == b"q" * 1000                        # held, not dropped
        with r.connect() as s2:
            s2.sendall(b"n")
            assert s2.recv(1) == b"n"
        s.close()
    finally:
        r.stop()
        up.close()


def test_stats_and_the_exit_line():
    up = Upstream("echo")
    r = Relay(up.port)
    try:
        with r.connect() as s:
            for _ in range(50):
                s.sendall(b"p" * 10)
                got = b""
                while len(got) < 10:
                    got += s.recv(10)
        st = r.cmd(cmd="stats")
        assert st["ok"] and st["up_bytes"] == 500 and st["down_bytes"] == 500, st
        assert st["own_us"]["n"] >= 100 and st["own_us"]["p50"] is not None, st
        assert st["own_us"]["p50"] <= st["own_us"]["p99"], st
        assert r.cmd(cmd="nope")["ok"] is False
        assert r.cmd(cmd="set", delay_us=5000, rate_mbit=100)["rate_mbit"] == 100
    finally:
        final = r.stop()
        up.close()
    assert final["connects"] == 1 and final["up_bytes"] == 500 and final["delay_us"] == 5000, final
    events = [json.loads(x)["event"] for x in open(os.path.join(r.dir, "log.jsonl"))]
    assert events[0] == "start" and events[-1] == "exit" and "connect" in events, events
    assert not os.path.exists(r.ctl)


def test_a_name_is_refused_where_an_address_belongs():
    p = subprocess.run([sys.executable, RELAY, "--listen", "localhost:9", "--to", "127.0.0.1:9"],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 2 and "IP:PORT literals" in p.stderr, p.stderr


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  {name:66s} ok")
            passed += 1
    print(f"{passed} passed")
