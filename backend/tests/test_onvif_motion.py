"""ONVIF parsing/auth and motion analysis."""

from __future__ import annotations

import base64
import hashlib

import numpy as np
from defusedxml.ElementTree import fromstring

from tapocloud.camera_service.detectors import MotionAnalyzer, sensitivity_to_area
from tapocloud.onvif.device import classify_notification, parse_notifications
from tapocloud.onvif.discovery import parse_probe_match
from tapocloud.onvif.soap import password_digest, security_header


def test_password_digest_matches_spec():
    nonce, created, pw = b"0123456789abcdef", "2026-10-14T21:00:00.000Z", "secret"
    expected = base64.b64encode(hashlib.sha1(nonce + created.encode() + pw.encode()).digest()).decode()
    assert password_digest(pw, nonce, created) == expected


def test_security_header_escapes_and_hides_password():
    from datetime import UTC, datetime

    hdr = security_header("ad<min", "plaintext-pw", datetime.now(UTC))
    assert "ad&lt;min" in hdr and "plaintext-pw" not in hdr and "PasswordDigest" in hdr


PULL_RESPONSE = b"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tev="http://www.onvif.org/ver10/events/wsdl"
 xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2" xmlns:tt="http://www.onvif.org/ver10/schema"
 xmlns:tns1="http://www.onvif.org/ver10/topics">
<s:Body><tev:PullMessagesResponse>
 <wsnt:NotificationMessage>
  <wsnt:Topic Dialect="http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet">tns1:RuleEngine/CellMotionDetector/Motion</wsnt:Topic>
  <wsnt:Message><tt:Message UtcTime="2026-10-14T21:04:12Z" PropertyOperation="Changed">
   <tt:Source><tt:SimpleItem Name="VideoSourceConfigurationToken" Value="vsconf"/></tt:Source>
   <tt:Data><tt:SimpleItem Name="IsMotion" Value="true"/></tt:Data>
  </tt:Message></wsnt:Message>
 </wsnt:NotificationMessage>
 <wsnt:NotificationMessage>
  <wsnt:Topic>tns1:RuleEngine/PeopleDetector/People</wsnt:Topic>
  <wsnt:Message><tt:Message UtcTime="2026-10-14T21:04:13Z"><tt:Data><tt:SimpleItem Name="IsPeople" Value="false"/></tt:Data></tt:Message></wsnt:Message>
 </wsnt:NotificationMessage>
</tev:PullMessagesResponse></s:Body></s:Envelope>"""


def test_parse_and_classify_notifications():
    notes = parse_notifications(fromstring(PULL_RESPONSE))
    assert len(notes) == 2
    assert notes[0].items["IsMotion"] == "true" and notes[0].utc_time.minute == 4
    assert classify_notification(notes[0]) == ("motion", True)
    assert classify_notification(notes[1]) == ("person", False)


PROBE_MATCH = b"""<?xml version="1.0" encoding="UTF-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"><SOAP-ENV:Body><d:ProbeMatches><d:ProbeMatch>
 <d:Scopes>onvif://www.onvif.org/name/TP-IPC onvif://www.onvif.org/hardware/C200 onvif://www.onvif.org/Profile/Streaming</d:Scopes>
 <d:XAddrs>http://192.168.1.31:2020/onvif/device_service</d:XAddrs></d:ProbeMatch></d:ProbeMatches></SOAP-ENV:Body></SOAP-ENV:Envelope>"""


def test_parse_probe_match():
    dev = parse_probe_match(PROBE_MATCH)
    assert dev.ip == "192.168.1.31" and dev.onvif_port == 2020
    assert dev.hardware == "C200" and dev.likely_tapo


def test_sensitivity_scale_is_monotonic():
    values = [sensitivity_to_area(s) for s in (1, 25, 50, 75, 100)]
    assert values == sorted(values, reverse=True)
    assert 0.001 < values[-1] < values[0] < 0.11


def test_motion_analyzer_detects_moving_object_and_ignores_lights():
    w, h = 160, 90
    rng = np.random.default_rng(0)
    bg = rng.integers(40, 60, size=(h, w), dtype=np.uint8)
    a = MotionAnalyzer(w, h, sensitivity=50)
    for _ in range(5):
        assert a.feed(bg.tobytes())[0] is False  # static scene
    detected = False
    for x in range(0, 60, 6):  # a bright 20x20 square walking across
        f = bg.copy()
        f[30:50, x : x + 20] = 230
        detected |= a.feed(f.tobytes())[0]
    assert detected
    # IR switch / lights on: whole image changes -> not motion.
    a2 = MotionAnalyzer(w, h, sensitivity=100)
    a2.feed(bg.tobytes())
    assert a2.feed(np.full((h, w), 250, dtype=np.uint8).tobytes())[0] is False
