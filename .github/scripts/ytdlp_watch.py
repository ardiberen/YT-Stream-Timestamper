"""Daily check used by .github/workflows/ytdlp-watch.yml: is there a newer yt-dlp than the one in the latest
release? YouTube changes things every few weeks and yt-dlp follows within days, so a new yt-dlp is the usual
reason the app suddenly can't download or read a stream.

    python ytdlp_watch.py <build-versions.txt of the latest release> <existing tags file>

Prints GitHub Actions outputs (key=value lines):
    need     "true" when a release should be made
    ytdlp    the yt-dlp version to build with (exact, so the build can't pick up an even newer, untested one)
    version  the app version to release
    bump     "true" when version.py must be raised first (the current version is already released)
    reason   one line for the run summary
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

COOLDOWN = timedelta(hours=24)  # let a fresh yt-dlp settle (broken releases usually get a fix within a day)
ROOT = Path(__file__).resolve().parents[2]


def vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def released_ytdlp(build_versions: str) -> str:
    m = re.search(r"^yt-dlp==(\S+)", build_versions, re.M)
    return m.group(1) if m else ""


def app_version(version_py: str) -> str:
    m = re.search(r'^APP_VERSION = "([^"]+)"', version_py, re.M)
    if not m:
        raise SystemExit("APP_VERSION not found in version.py")
    return m.group(1)


def bump_patch(v: str) -> str:
    parts = v.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def decide(latest: str, uploaded: datetime, released: str, current: str, tags: set, now: datetime) -> dict:
    out = {"need": "false", "ytdlp": latest, "version": current, "bump": "false"}
    if not released:
        out["reason"] = "the latest release has no yt-dlp version listed; nothing to compare"
    elif vtuple(latest) <= vtuple(released):
        out["reason"] = f"yt-dlp {released} in the latest release is the newest"
    elif now - uploaded < COOLDOWN:
        out["reason"] = f"yt-dlp {latest} is newer but less than a day old; checking again tomorrow"
    else:
        out["need"] = "true"
        if f"v{current}" in tags:
            out["version"], out["bump"] = bump_patch(current), "true"
        out["reason"] = f"yt-dlp {released} -> {latest}: releasing v{out['version']}"
    return out


def pypi_latest() -> tuple:
    with urllib.request.urlopen("https://pypi.org/pypi/yt-dlp/json", timeout=60) as r:
        d = json.load(r)
    v = d["info"]["version"]
    times = [f["upload_time_iso_8601"] for f in d["releases"][v]]
    uploaded = max(datetime.fromisoformat(t.replace("Z", "+00:00")) for t in times)
    return v, uploaded


def main(argv) -> None:
    released = released_ytdlp(Path(argv[1]).read_text(encoding="utf-8", errors="replace"))
    tags = set(Path(argv[2]).read_text().split())
    current = app_version((ROOT / "version.py").read_text(encoding="utf-8"))
    latest, uploaded = pypi_latest()
    for k, v in decide(latest, uploaded, released, current, tags, datetime.now(timezone.utc)).items():
        print(f"{k}={v}")


if __name__ == "__main__":
    main(sys.argv)
