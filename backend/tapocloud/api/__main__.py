"""``python -m tapocloud.api`` — start the backend (API + uploader + UI)."""

import uvicorn

from tapocloud.config import get_settings
from tapocloud.logging_setup import setup_logging


def main() -> None:
    s = get_settings()
    setup_logging(s.log_level, s.logs_dir, "backend")
    uvicorn.run(
        "tapocloud.api.main:app",
        host=s.api_host,
        port=s.api_port,
        log_config=None,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips=s.forwarded_allow_ips,
    )


if __name__ == "__main__":
    main()
