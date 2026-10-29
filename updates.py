"""'Is there a newer version?' check against this project's GitHub Releases page.

Safety: it only READS the version number of the newest published release (one small HTTPS request to
api.github.com). It never downloads, installs or runs anything; the user is just shown a link to the
Releases page. The link is built here from the repo name baked into the build, never taken from the
response, and the version text must look like 1.2.3 or it is ignored.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

API_URL = "https://api.github.com/repos/{repo}/releases/latest"  # newest non-draft, non-prerelease release
REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
TAG_RE = re.compile(r"^v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")
CHECK_EVERY_SECONDS = 24 * 3600
MAX_BYTES = 1_000_000


class _Refused(Exception):
    pass


def parse_version(text) -> Optional[tuple[int, int, int]]:
    m = TAG_RE.match(str(text or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def fmt_version(v: tuple[int, int, int]) -> str:
    return ".".join(str(x) for x in v)


def repo_ok(repo: str) -> bool:
    return bool(REPO_RE.match(repo or ""))


def releases_page(repo: str) -> str:
    return f"https://github.com/{repo}/releases/latest"


def help_page(repo: str) -> str:
    return f"https://github.com/{repo}#getting-help"


def is_due(last_check, now: Optional[float] = None) -> bool:
    try:
        last = float(last_check or 0)
    except (TypeError, ValueError):
        last = 0.0
    now = time.time() if now is None else now
    return now - last >= CHECK_EVERY_SECONDS or last > now  # a clock set backwards shouldn't block checks forever


class _GitHubOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """GitHub may redirect a renamed repo; only follow redirects that stay on https://api.github.com."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p = urllib.parse.urlsplit(newurl)
        if p.scheme != "https" or (p.hostname or "").lower() != "api.github.com":
            raise _Refused("redirected away from api.github.com")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_latest_tag(repo: str, timeout: float = 8) -> Optional[str]:
    if not repo_ok(repo):
        return None
    req = urllib.request.Request(API_URL.format(repo=repo), headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "StreamTimestamper-update-check",
    })
    opener = urllib.request.build_opener(_GitHubOnlyRedirects)
    with opener.open(req, timeout=timeout) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return None
    obj = json.loads(data.decode("utf-8"))
    tag = obj.get("tag_name") if isinstance(obj, dict) else None
    return tag if isinstance(tag, str) else None


def check(repo: str, current: str) -> tuple[str, Optional[str]]:
    """-> ("newer", "1.2.3") | ("current", None) | ("failed", None). Never raises."""
    try:
        cur = parse_version(current)
        new = parse_version(fetch_latest_tag(repo))
        if not cur or not new:
            return "failed", None
        return ("newer", fmt_version(new)) if new > cur else ("current", None)
    except Exception:  # noqa: BLE001  (offline, 404 = no releases yet, rate-limited, bad JSON ...)
        return "failed", None
