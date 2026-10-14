"""tapo-lab — EXPERIMENTAL interoperability layer for *your own* Tapo cameras.

Everything in this package is EXPERIMENTAL:

* It is completely separate from the stable core (``tapocloud``). The core
  never imports it; the backend mounts its API only when
  ``TAPOCLOUD_TAPO_LAB_ENABLED=true`` *and* the package is installed.
* It only talks to devices that are already registered in Tapo Cloud
  Personal and that live on a private network (see :mod:`tapo_lab.guard`).
* It never bypasses authentication: it uses credentials *you* provide for
  *your* devices, through the camera's local API, via the open-source
  ``pytapo`` client.
* Every call is written to an audit log (``<data_dir>/logs/tapo-lab-audit.log``).

Out of scope, by design: attacking TP-Link servers, accessing other people's
devices or accounts, extracting third-party credentials, circumventing
payments/Tapo Care, DRM or any authentication mechanism.
"""

EXPERIMENTAL = True
__version__ = "0.0.1"


def is_enabled() -> bool:
    from tapocloud.config import get_settings

    return bool(get_settings().tapo_lab_enabled)
