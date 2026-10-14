"""Unit tests for pure building blocks (no network, no FFmpeg)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tapocloud.camera_service.schedule import is_active, validate
from tapocloud.core import crypto, security
from tapocloud.core.ffmpeg import audio_args, continuous_segment_cmd, parse_probe_output, video_args
from tapocloud.core.naming import recording_relpath, safe_join, slugify
from tapocloud.core.rtsp import build_camera_url, mask_url
from tapocloud.logging_setup import redact


# -- crypto / security ------------------------------------------------------
def test_encrypt_roundtrip():
    token = crypto.encrypt("s3cr3t-pässword")
    assert "s3cr3t" not in token
    assert crypto.decrypt(token) == "s3cr3t-pässword"


def test_decrypt_tampered_fails():
    token = crypto.encrypt("x")
    with pytest.raises(crypto.SecretError):
        crypto.decrypt(token[:-4] + "AAAA")


def test_password_hash():
    h = security.hash_password("correct horse")
    assert h.startswith("scrypt$")
    assert security.verify_password("correct horse", h)
    assert not security.verify_password("wrong", h)
    assert not security.verify_password("x", "garbage")


def test_stream_token(monkeypatch):
    tok = security.make_stream_token(3, ttl_seconds=60)
    assert security.verify_stream_token(tok, 3)
    assert not security.verify_stream_token(tok, 4)  # bound to its camera
    assert not security.verify_stream_token(tok[:-2] + "xx", 3)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 120)
    assert not security.verify_stream_token(tok, 3)  # expired


# -- redaction ----------------------------------------------------------------
def test_redact_rtsp_and_kv():
    line = "Error opening rtsp://admin:P@ss%40w0rd@192.168.1.20:554/stream1 password=hunter2 token: abc"
    out = redact(line)
    assert "P@ss" not in out and "hunter2" not in out and "abc" not in out
    assert "rtsp://admin:***@" in out


def test_build_and_mask_url():
    url = build_camera_url("192.168.1.20", 554, "/stream1", "admin", "p@ss:/word")
    assert url == "rtsp://admin:p%40ss%3A%2Fword@192.168.1.20:554/stream1"
    assert mask_url(url) == "rtsp://admin:***@192.168.1.20:554/stream1"


# -- naming -------------------------------------------------------------------
def test_slugify():
    assert slugify("Salón principal") == "Salon_principal"
    assert slugify("../../etc") == "etc"
    assert slugify("CON") == "CON_cam"
    assert slugify("💡") == "camera"


def test_recording_relpath():
    p = recording_relpath("Salon", datetime(2026, 10, 14, 23, 5, 0))
    assert str(p) == "Salon/2026/10/14/23-05-00.mp4"
    assert str(recording_relpath("Salon", datetime(2026, 10, 14, 23, 5, 0), "event")).endswith("23-05-00_event.mp4")


def test_safe_join(tmp_path: Path):
    assert safe_join(tmp_path, "a/b.mp4") == (tmp_path / "a/b.mp4").resolve()
    with pytest.raises(ValueError):
        safe_join(tmp_path, "../outside.mp4")
    with pytest.raises(ValueError):
        safe_join(tmp_path, "/etc/passwd")


# -- schedule -----------------------------------------------------------------
NIGHTS = [{"days": [0, 1, 2, 3, 4], "start": "22:00", "end": "08:00"}, {"days": [5, 6], "start": "00:00", "end": "24:00"}]


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (datetime(2026, 10, 12, 23, 0), True),  # Monday 23:00
        (datetime(2026, 10, 13, 7, 59), True),  # Tuesday 07:59 (Monday's night)
        (datetime(2026, 10, 13, 8, 0), False),  # Tuesday 08:00
        (datetime(2026, 10, 14, 12, 0), False),  # Wednesday noon
        (datetime(2026, 10, 17, 12, 0), True),  # Saturday (all day)
        (datetime(2026, 10, 12, 3, 0), False),  # Monday 03:00: Sunday has no overnight window
        (datetime(2026, 10, 17, 7, 0), True),  # Saturday 07:00 (Friday night + weekend)
    ],
)
def test_schedule(when, expected):
    assert is_active(NIGHTS, when) is expected


def test_schedule_validation():
    assert validate([{"days": [6, 0, 0], "start": "08:00", "end": "09:30"}]) == [
        {"days": [0, 6], "start": "08:00", "end": "09:30"}
    ]
    for bad in ([{"days": [], "start": "08:00", "end": "09:00"}], [{"days": [1], "start": "25:00", "end": "09:00"}],
                [{"days": [1], "start": "09:00", "end": "09:00"}]):  # fmt: skip
        with pytest.raises(ValueError):
            validate(bad)


# -- ffmpeg -------------------------------------------------------------------
def test_parse_probe():
    data = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 2304, "height": 1296, "avg_frame_rate": "15/1"},
            {"codec_type": "audio", "codec_name": "pcm_alaw", "sample_rate": "8000"},
        ],
        "format": {"bit_rate": "2100000"},
    }
    info = parse_probe_output(data)
    assert info.available and info.codec == "h264" and info.fps == 15 and info.bitrate_kbps == 2100
    assert info.audio_codec == "pcm_alaw" and info.audio_sample_rate == 8000


def test_audio_video_args():
    assert audio_args("pcm_alaw", True) == ["-c:a", "aac", "-b:a", "64k"]
    assert audio_args("aac", True) == ["-c:a", "copy"]
    assert audio_args("pcm_alaw", False) == ["-an"]
    assert audio_args(None, True) == ["-an"]
    assert video_args("hevc") == ["-c:v", "copy", "-tag:v", "hvc1"]


def test_continuous_cmd_never_reencodes_video(tmp_path):
    cmd = continuous_segment_cmd("rtsp://x/y", tmp_path / "%Y.mp4", tmp_path / "l.csv", 300, "h264", "pcm_alaw", True)
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert "-segment_atclocktime" in cmd and "300" in cmd
    assert any("frag_keyframe" in c for c in cmd)
    assert cmd[cmd.index("-rtsp_transport") + 1] == "tcp"


# -- retention ------------------------------------------------------------------
def _rec(**kw):
    from tapocloud.core.models import Recording

    now = datetime.now(UTC)
    base = dict(kind="continuous", status="complete", started_at=now - timedelta(hours=2), ended_at=now - timedelta(hours=2),
                local_deleted_at=None, uploaded_at=None)  # fmt: skip
    base.update(kw)
    return Recording(**base)


def test_retention_never_deletes_unuploaded():
    from tapocloud.storage.retention import is_deletable

    now = datetime.now(UTC)
    old = _rec(started_at=now - timedelta(days=60), ended_at=now - timedelta(days=60))
    assert not is_deletable(
        old, drive_enabled=True, upload_kinds=["continuous"], retention_hours=0, rotate_without_drive=True, now=now
    )


def test_retention_rules():
    from tapocloud.storage.retention import is_deletable

    now = datetime.now(UTC)
    kw = dict(drive_enabled=True, upload_kinds=["continuous"], rotate_without_drive=False, now=now)
    uploaded = _rec(uploaded_at=now)
    assert is_deletable(uploaded, retention_hours=0, **kw)  # until_uploaded
    assert is_deletable(uploaded, retention_hours=1, **kw)  # ended 2h ago, keep 1h
    assert not is_deletable(uploaded, retention_hours=24, **kw)
    # Drive disabled: only with explicit rotation.
    kw2 = dict(kw, drive_enabled=False)
    assert not is_deletable(_rec(), retention_hours=1, **kw2)
    assert is_deletable(_rec(), retention_hours=1, **dict(kw2, rotate_without_drive=True))
    assert not is_deletable(_rec(status="recording"), retention_hours=0, **dict(kw2, rotate_without_drive=True))


# -- uploader -------------------------------------------------------------------
def test_backoff_and_folders():
    from tapocloud.drive.uploader import backoff_seconds, drive_folder_parts

    assert 24 <= backoff_seconds(1) <= 36
    assert backoff_seconds(30) <= 3600 * 1.2
    assert drive_folder_parts("TapoCloud", "Salon/2026/10/14/23-00-00.mp4") == ["TapoCloud", "Salon", "2026", "10", "14"]
