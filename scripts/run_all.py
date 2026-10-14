#!/usr/bin/env python3
"""Start and supervise the whole native stack (Windows, Linux, macOS, Raspberry Pi).

    python scripts/run_all.py [--open]

Processes (each restarted automatically if it crashes):
  1. MediaMTX           streaming relay        (bin/mediamtx[.exe] or PATH)
  2. tapocloud.api      backend + UI on :8000
  3. tapocloud.camera_service   recorders, detectors, health checks

Ctrl+C stops everything cleanly (FFmpeg finalises the files being written).
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
EXE = ".exe" if os.name == "nt" else ""


def find_binary(name: str) -> str | None:
    local = BIN / f"{name}{EXE}"
    if local.exists():
        return str(local)
    return shutil.which(name)


class Child:
    def __init__(self, name: str, cmd: list[str], env: dict[str, str]) -> None:
        self.name, self.cmd, self.env = name, cmd, env
        self.proc: subprocess.Popen | None = None
        self.backoff = 2.0
        self.started = 0.0
        self.next_start = 0.0

    def start(self) -> None:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.proc = subprocess.Popen(self.cmd, cwd=ROOT, env=self.env, creationflags=flags)
        self.started = time.monotonic()
        print(f"[run_all] started {self.name} (pid {self.proc.pid})", flush=True)

    def poll(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        now = time.monotonic()
        if self.proc is not None:
            code = self.proc.returncode
            self.proc = None
            if now - self.started > 60:
                self.backoff = 2.0
            print(f"[run_all] {self.name} exited with code {code}; restarting in {self.backoff:.0f}s", flush=True)
            self.next_start = now + self.backoff
            self.backoff = min(self.backoff * 2, 60)
        if now >= self.next_start:
            self.start()

    def stop(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        if os.name == "nt":
            self.proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=30)  # camera-service needs a moment to close files
        except subprocess.TimeoutExpired:
            self.proc.kill()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--open", action="store_true", help="open the UI in the browser")
    ap.add_argument("--no-mediamtx", action="store_true", help="MediaMTX is managed elsewhere")
    args = ap.parse_args()

    env = dict(os.environ)
    ffmpeg, ffprobe = find_binary("ffmpeg"), find_binary("ffprobe")
    if not ffmpeg or not ffprobe:
        print("ERROR: FFmpeg not found. Run scripts/install-windows.ps1 or install ffmpeg.", file=sys.stderr)
        return 1
    env.setdefault("TAPOCLOUD_FFMPEG_PATH", ffmpeg)
    env.setdefault("TAPOCLOUD_FFPROBE_PATH", ffprobe)
    env.setdefault("TAPOCLOUD_FRONTEND_DIR", str(ROOT / "frontend" / "out"))
    if not (ROOT / "frontend" / "out" / "index.html").exists():
        print("WARNING: frontend not built (frontend/out). Run: cd frontend && npm ci && npm run build", file=sys.stderr)

    children: list[Child] = []
    if not args.no_mediamtx:
        mediamtx = find_binary("mediamtx")
        if not mediamtx:
            print("ERROR: MediaMTX not found in bin/ or PATH.", file=sys.stderr)
            return 1
        children.append(Child("mediamtx", [mediamtx, str(ROOT / "config" / "mediamtx.yml")], env))
    py = sys.executable
    children.append(Child("backend", [py, "-m", "tapocloud.api"], env))
    children.append(Child("camera-service", [py, "-m", "tapocloud.camera_service"], env))

    stopping = False

    def handle_stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    for c in children:
        c.start()
    if args.open:
        time.sleep(3)
        webbrowser.open("http://localhost:8000/")
    print("[run_all] Tapo Cloud Personal running on http://localhost:8000  (Ctrl+C to stop)", flush=True)
    try:
        while not stopping:
            for c in children:
                c.poll()
            time.sleep(1)
    finally:
        print("[run_all] stopping...", flush=True)
        for c in reversed(children):  # camera-service first, MediaMTX last
            c.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
