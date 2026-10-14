"""FFmpeg/ffprobe integration: stream probing, command builders and a
supervised subprocess wrapper.

Key decisions
-------------
* **Never re-encode video** (``-c:v copy``): a Raspberry Pi can then record
  16 cameras, because FFmpeg only re-muxes packets.
* **Audio**: Tapo sends G.711 (``pcm_alaw``) which MP4 cannot hold, so audio
  is converted to AAC (cheap: 8 kHz mono). AAC sources are copied as-is.
* **Fragmented MP4** (``frag_keyframe+empty_moov``): if power is lost while
  writing, everything up to the last fragment is still playable. A classic
  MP4 without its final ``moov`` atom would be unreadable.
* **H.265** is tagged ``hvc1`` so it plays in Safari/QuickTime.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tapocloud.config import get_settings
from tapocloud.logging_setup import redact

log = logging.getLogger(__name__)

# Fragmented MP4 flags (see module docstring).
FMP4_FLAGS = "+frag_keyframe+empty_moov+default_base_moof"

_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


# --------------------------------------------------------------------------
# Probing
# --------------------------------------------------------------------------
@dataclass
class StreamInfo:
    available: bool
    codec: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    bitrate_kbps: int | None = None
    audio_codec: str | None = None
    audio_sample_rate: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def _parse_rate(rate: str | None) -> float | None:
    if not rate or rate in ("0/0", "0"):
        return None
    try:
        num, _, den = rate.partition("/")
        val = float(num) / float(den or 1)
        return round(val, 2) if 0 < val < 300 else None
    except (ValueError, ZeroDivisionError):
        return None


def parse_probe_output(data: dict[str, Any]) -> StreamInfo:
    """Turn ``ffprobe -of json`` output into a :class:`StreamInfo`."""
    info = StreamInfo(available=False)
    for st in data.get("streams", []):
        if st.get("codec_type") == "video" and info.codec is None:
            info.available = True
            info.codec = st.get("codec_name")
            info.width, info.height = st.get("width"), st.get("height")
            info.fps = _parse_rate(st.get("avg_frame_rate")) or _parse_rate(st.get("r_frame_rate"))
            if st.get("bit_rate"):
                info.bitrate_kbps = int(st["bit_rate"]) // 1000
        elif st.get("codec_type") == "audio" and info.audio_codec is None:
            info.audio_codec = st.get("codec_name")
            sr = st.get("sample_rate")
            info.audio_sample_rate = int(sr) if sr else None
    fmt_br = data.get("format", {}).get("bit_rate")
    if info.bitrate_kbps is None and fmt_br:
        info.bitrate_kbps = int(fmt_br) // 1000
    return info


def probe(url: str, timeout_s: float = 15) -> StreamInfo:
    """Detect codec, resolution, FPS and audio of an RTSP URL or file."""
    s = get_settings()
    cmd = [s.ffprobe_path, "-v", "error", "-show_streams", "-show_format", "-of", "json"]
    if url.startswith("rtsp://"):
        cmd += ["-rtsp_transport", "tcp", "-timeout", str(int(timeout_s * 1_000_000))]
    cmd += ["-i", url]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout_s + 5, check=False, creationflags=_CREATIONFLAGS)
    except FileNotFoundError:
        return StreamInfo(available=False, error="ffprobe not found")
    except subprocess.TimeoutExpired:
        return StreamInfo(available=False, error="timeout")
    if out.returncode != 0:
        err = redact(out.stderr.decode(errors="replace").strip().splitlines()[-1:][0] if out.stderr else "ffprobe failed")
        return StreamInfo(available=False, error=_humanize_error(err))
    try:
        return parse_probe_output(json.loads(out.stdout or b"{}"))
    except json.JSONDecodeError:
        return StreamInfo(available=False, error="invalid ffprobe output")


def _humanize_error(err: str) -> str:
    low = err.lower()
    if "401" in low or "unauthorized" in low:
        return "Credenciales incorrectas (401). Revisa la 'cuenta de cámara' creada en la app Tapo."
    if "404" in low or "not found" in low:
        return "Ruta de stream no encontrada (404). Prueba stream1 / stream2."
    if "connection refused" in low:
        return "Conexión rechazada: ¿puerto RTSP correcto (554)?"
    # Windows FFmpeg reports socket errors as negative errno values:
    # -138 / -10060 = timed out, -10061 = refused, -10065/-10051 = unreachable.
    if any(code in low for code in ("error number -138", "-10060", "-10065", "-10051")):
        return "La cámara no responde en esa IP: comprueba la IP y que el PC y la cámara estén en la misma red."
    if "-10061" in low:
        return "Conexión rechazada: ¿puerto RTSP correcto (554)?"
    if "timed out" in low or "timeout" in low:
        return "Tiempo de espera agotado: la cámara no responde."
    return err[:200]


# --------------------------------------------------------------------------
# Command builders (pure functions — unit tested)
# --------------------------------------------------------------------------
def _input_args(url: str) -> list[str]:
    args = ["-hide_banner", "-nostats", "-loglevel", "warning"]
    if url.startswith("rtsp://"):
        # TCP avoids packet loss artefacts on Wi-Fi; 10 s socket timeout so a
        # dead camera ends the process (the supervisor then reconnects).
        args += ["-rtsp_transport", "tcp", "-timeout", "10000000"]
    # Regenerate missing timestamps (some firmwares send broken PTS).
    args += ["-fflags", "+genpts", "-i", url]
    return args


def audio_args(audio_codec: str | None, record_audio: bool) -> list[str]:
    if not record_audio or not audio_codec:
        return ["-an"]
    if audio_codec == "aac":
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", "64k"]


def video_args(video_codec: str | None) -> list[str]:
    args = ["-c:v", "copy"]
    if video_codec in ("hevc", "h265"):
        args += ["-tag:v", "hvc1"]
    return args


def continuous_segment_cmd(
    url: str,
    out_pattern: Path,
    segment_list: Path,
    segment_seconds: int,
    video_codec: str | None,
    audio_codec: str | None,
    record_audio: bool,
) -> list[str]:
    """24/7 recording split into clock-aligned MP4 segments.

    ``-segment_atclocktime`` aligns cuts to wall-clock multiples (23:00, 23:05...)
    and ``-strftime`` names files by their start time. The CSV segment list is
    appended when each segment is *closed*, which is how the camera-service
    learns a file is complete and can be queued for upload.
    """
    s = get_settings()
    return [
        s.ffmpeg_path,
        *_input_args(url),
        "-map", "0:v:0", "-map", "0:a:0?",
        *video_args(video_codec),
        *audio_args(audio_codec, record_audio),
        "-f", "segment",
        "-segment_time", str(segment_seconds),
        "-segment_atclocktime", "1",
        "-reset_timestamps", "1",
        "-strftime", "1",
        "-segment_format", "mp4",
        "-segment_format_options", f"movflags={FMP4_FLAGS}",
        "-segment_list", str(segment_list),
        "-segment_list_type", "csv",
        "-segment_list_flags", "+live",
        str(out_pattern),
    ]  # fmt: skip


def ring_buffer_cmd(
    url: str,
    ring_dir: Path,
    segment_list: Path,
    chunk_seconds: int,
    wrap: int,
    video_codec: str | None,
    audio_codec: str | None,
    record_audio: bool,
) -> list[str]:
    """Pre-event circular buffer: short MPEG-TS chunks overwritten in a ring.

    MPEG-TS chunks concatenate losslessly, so an event clip is assembled from
    "the chunks before the event" + "the chunks after" without re-encoding.
    Audio is converted to AAC here once, so the final concat is a pure copy.
    """
    s = get_settings()
    vargs = ["-c:v", "copy"]
    return [
        s.ffmpeg_path,
        *_input_args(url),
        "-map", "0:v:0", "-map", "0:a:0?",
        *vargs,
        *audio_args(audio_codec, record_audio),
        "-f", "segment",
        "-segment_time", str(chunk_seconds),
        "-segment_wrap", str(wrap),
        "-reset_timestamps", "1",
        "-segment_format", "mpegts",
        "-segment_list", str(segment_list),
        "-segment_list_type", "csv",
        "-segment_list_flags", "+live",
        str(ring_dir / "chunk_%04d.ts"),
    ]  # fmt: skip


def single_file_cmd(
    url: str,
    out_file: Path,
    max_seconds: int,
    video_codec: str | None,
    audio_codec: str | None,
    record_audio: bool,
) -> list[str]:
    """Manual recording to one fragmented MP4 (stopped with 'q' on stdin or ``-t``)."""
    s = get_settings()
    return [
        s.ffmpeg_path,
        *_input_args(url),
        "-map", "0:v:0", "-map", "0:a:0?",
        *video_args(video_codec),
        *audio_args(audio_codec, record_audio),
        "-t", str(max_seconds),
        "-movflags", FMP4_FLAGS,
        "-f", "mp4", "-y", str(out_file),
    ]  # fmt: skip


def concat_cmd(list_file: Path, out_file: Path, video_codec: str | None) -> list[str]:
    """Losslessly join TS chunks into one MP4 (event clips)."""
    s = get_settings()
    return [
        s.ffmpeg_path, "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-map", "0:v:0", "-map", "0:a:0?",
        *video_args(video_codec), "-c:a", "copy",
        "-movflags", "+faststart",
        "-f", "mp4", "-y", str(out_file),
    ]  # fmt: skip


def snapshot_cmd(url: str, out_file: Path, width: int | None = None) -> list[str]:
    s = get_settings()
    vf = ["-vf", f"scale={width}:-2"] if width else []
    return [
        s.ffmpeg_path, *_input_args(url),
        "-frames:v", "1", *vf, "-q:v", "3", "-update", "1", "-y", str(out_file),
    ]  # fmt: skip


def thumbnail_cmd(video_file: Path, out_file: Path, offset_s: float = 1.0, width: int = 480) -> list[str]:
    s = get_settings()
    return [
        s.ffmpeg_path, "-hide_banner", "-loglevel", "error",
        "-ss", f"{offset_s:.2f}", "-i", str(video_file),
        "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", "-update", "1", "-y", str(out_file),
    ]  # fmt: skip


def motion_frames_cmd(url: str, width: int, height: int, fps: float) -> list[str]:
    """Decode a (sub) stream to tiny grayscale raw frames on stdout for motion analysis.

    Decoding a 640x360 sub-stream at 4 fps costs ~1-3 % of one CPU core.
    """
    s = get_settings()
    return [
        s.ffmpeg_path, *_input_args(url),
        "-an", "-vf", f"fps={fps},scale={width}:{height},format=gray",
        "-f", "rawvideo", "pipe:1",
    ]  # fmt: skip


def run_quick(cmd: list[str], timeout_s: float = 20) -> tuple[bool, str]:
    """Run a short FFmpeg job (snapshot, thumbnail, concat). Returns ``(ok, error)``."""
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout_s, check=False, creationflags=_CREATIONFLAGS)
    except FileNotFoundError:
        return False, "ffmpeg not found"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    if res.returncode != 0:
        return False, redact(res.stderr.decode(errors="replace").strip()[-300:])
    return True, ""


# --------------------------------------------------------------------------
# Long-running process wrapper
# --------------------------------------------------------------------------
@dataclass
class FFmpegProcess:
    """A long-running FFmpeg child with captured (redacted) stderr.

    Each camera has its *own* processes, so a misbehaving stream can crash or
    hang without affecting any other camera.
    """

    name: str
    cmd: list[str]
    stdout_pipe: bool = False
    proc: subprocess.Popen | None = None
    stderr_tail: deque[str] = field(default_factory=lambda: deque(maxlen=40))
    _reader: threading.Thread | None = None

    def start(self) -> None:
        log.debug("Starting %s", self.name)
        self.proc = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE if self.stdout_pipe else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=_CREATIONFLAGS,
            bufsize=0,
        )
        self._reader = threading.Thread(target=self._drain_stderr, name=f"{self.name}-stderr", daemon=True)
        self._reader.start()

    def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        for raw in iter(self.proc.stderr.readline, b""):
            line = redact(raw.decode(errors="replace").rstrip())
            if line:
                self.stderr_tail.append(line)
                log.debug("[%s] %s", self.name, line)

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def returncode(self) -> int | None:
        return self.proc.poll() if self.proc else None

    def last_error(self) -> str:
        return self.stderr_tail[-1] if self.stderr_tail else ""

    def stop(self, timeout_s: float = 8) -> None:
        """Graceful stop: 'q' lets FFmpeg finalize the current file properly."""
        if not self.proc or self.proc.poll() is not None:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.write(b"q")
                self.proc.stdin.flush()
                self.proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            self.proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            log.warning("%s did not stop gracefully; terminating", self.name)
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def which_ffmpeg() -> dict[str, str | None]:
    """Resolve binaries for the system status page."""
    import shutil

    s = get_settings()
    return {
        "ffmpeg": shutil.which(s.ffmpeg_path) or (s.ffmpeg_path if os.path.isfile(s.ffmpeg_path) else None),
        "ffprobe": shutil.which(s.ffprobe_path) or (s.ffprobe_path if os.path.isfile(s.ffprobe_path) else None),
    }
