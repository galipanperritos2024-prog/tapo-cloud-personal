#!/usr/bin/env python3
"""Fake Tapo camera for development and testing — no hardware needed.

Starts a *separate* MediaMTX instance that behaves like a Tapo camera:

    rtsp://camuser:campass@127.0.0.1:8654/stream1   1280x720 H.264 15 fps + G.711 A-law audio
    rtsp://camuser:campass@127.0.0.1:8654/stream2    640x360 H.264 15 fps

The picture is FFmpeg's animated test pattern (with moving elements, so the
motion detector fires). Add it in the UI with IP ``127.0.0.1``, RTSP port
``8654``, ONVIF disabled, and start the backend with
``TAPOCLOUD_ALLOW_PUBLIC_CAMERA_IPS=true`` (loopback is not a LAN address).

Usage::

    python scripts/fake_camera.py [--mediamtx PATH] [--ffmpeg PATH] [--port 8654]
"""

from __future__ import annotations

import argparse
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CONFIG = """
logLevel: warn
rtsp: yes
rtspTransports: [tcp]
rtspAddress: 127.0.0.1:{port}
rtmp: no
hls: no
webrtc: no
srt: no
api: no
authMethod: internal
authInternalUsers:
  - user: {user}
    pass: {password}
    permissions: [{{action: read}}]
  - user: any
    ips: ['127.0.0.1', '::1']
    permissions: [{{action: publish}}]
paths:
  stream1: {{}}
  stream2: {{}}
"""


def publisher(ffmpeg: str, port: int, path: str, size: str, with_audio: bool) -> list[str]:
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-re",
        "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=15",
    ]  # fmt: skip
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=8000"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-g", "30", "-pix_fmt", "yuv420p"]
    if with_audio:
        cmd += ["-c:a", "pcm_alaw", "-ar", "8000", "-ac", "1"]
    cmd += ["-f", "rtsp", "-rtsp_transport", "tcp", f"rtsp://127.0.0.1:{port}/{path}"]
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mediamtx", default=shutil.which("mediamtx") or "mediamtx")
    ap.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg")
    ap.add_argument("--port", type=int, default=8654)
    ap.add_argument("--user", default="camuser")
    ap.add_argument("--password", default="campass")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="fakecam-"))
    conf = tmp / "fakecam.yml"
    conf.write_text(CONFIG.format(port=args.port, user=args.user, password=args.password), encoding="utf-8")

    procs: list[subprocess.Popen] = []
    try:
        procs.append(subprocess.Popen([args.mediamtx, str(conf)]))
        time.sleep(1.5)
        procs.append(subprocess.Popen(publisher(args.ffmpeg, args.port, "stream1", "1280x720", True), stdin=subprocess.DEVNULL))
        procs.append(subprocess.Popen(publisher(args.ffmpeg, args.port, "stream2", "640x360", False), stdin=subprocess.DEVNULL))
        print(f"Fake camera ready: rtsp://{args.user}:{args.password}@127.0.0.1:{args.port}/stream1 (Ctrl+C to stop)")
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        print("A fake camera process exited", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for p in reversed(procs):
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
