"""TLS settings for the app's own HTTPS requests (caption download, update check).

Python's urllib trusts only the Windows certificate store. On a PC whose store is missing a root certificate
(an old or rarely updated Windows, or a locked-down one) that gives
"CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate" while yt-dlp and the Gemini library, which
carry their own certificate list, work fine. So trust both: the Windows store AND the certifi list shipped
with the app. Certificate and host-name checking stay fully on; this only widens which public root
certificates are accepted, it never turns verification off.
"""
from __future__ import annotations

import ssl
import urllib.request


def make_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()  # Windows store (or the OS default elsewhere), verification on
    try:
        import certifi

        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:  # noqa: BLE001  (certifi missing/unreadable: keep the system store only)
        pass
    return ctx


def https_handler() -> urllib.request.HTTPSHandler:
    return urllib.request.HTTPSHandler(context=make_context())
