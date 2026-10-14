"""Camera service: an independent process that owns every long-running FFmpeg
job (recording, pre-event buffers, motion analysis) and the camera health checks.

Run with ``python -m tapocloud.camera_service``.
"""
