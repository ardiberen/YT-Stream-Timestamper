"""Extraction engine: YouTube captions (yt-dlp, logged out) or caption files -> Gemini -> text file.

No GUI code in here. yt_dlp and google-genai are imported lazily so the pure helpers
(URL parsing, caption parsing, etc.) can be tested without them installed.

Account safety: this module never sends a YouTube/Google login. yt-dlp is always run with
--ignore-config --no-cookies --no-cookies-from-browser, so a stray yt-dlp config file on the PC
can't add one either.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# --------------------------------------------------------------------------- errors


class StopRequested(Exception):
    """User pressed Stop."""


class YouTubeBlocked(Exception):
    """YouTube is rate-limiting / bot-checking this internet connection. Stop all YouTube requests."""


class ApiKeyProblem(Exception):
    """Gemini rejected the API key."""


# --------------------------------------------------------------------------- config


@dataclass
class RunConfig:
    api_key: str
    model: str
    prompt: str
    output_dir: str
    min_stream_minutes: float = 40
    min_segment_minutes: float = 4
    caption_lang: str = "en"
    delay_min: float = 20
    delay_max: float = 40
    channel_tab: str = "streams"
    max_videos: int = 10
    max_per_run: int = 20
    skip_existing: bool = True
    save_transcript: bool = False
    max_retries: int = 5

    @classmethod
    def from_settings(cls, s: dict) -> "RunConfig":
        return cls(
            api_key=str(s.get("api_key", "")).strip(),
            model=str(s.get("model", "")).strip(),
            prompt=str(s.get("draft_prompt", "")),
            output_dir=str(s.get("output_dir", "")).strip(),
            min_stream_minutes=float(s.get("min_stream_minutes", 40)),
            min_segment_minutes=float(s.get("min_segment_minutes", 4)),
            caption_lang=str(s.get("caption_lang", "en")).strip() or "en",
            delay_min=float(s.get("delay_min", 20)),
            delay_max=float(s.get("delay_max", 40)),
            channel_tab=str(s.get("channel_tab", "streams")),
            max_videos=int(s.get("max_videos", 10)),
            max_per_run=int(s.get("max_per_run", 20)),
            skip_existing=bool(s.get("skip_existing", True)),
            save_transcript=bool(s.get("save_transcript", False)),
            max_retries=int(s.get("max_retries", 5)),
        )


# --------------------------------------------------------------------------- pure helpers

VIDEO_ID_RE = re.compile(r"(?:[?&]v=|youtu\.be/|/live/|/embed/|/shorts/)([0-9A-Za-z_-]{11})(?![0-9A-Za-z_-])")
BARE_ID_RE = re.compile(r"[0-9A-Za-z_-]{11}")
CHANNEL_TABS = ("videos", "streams", "shorts", "live", "playlists", "featured", "podcasts", "releases")
# Only these hosts count as YouTube links. Anything else is ignored, never handed to yt-dlp.
YT_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be",
                      "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com"})
YT_BASE = "https://www.youtube.com"
_LIST_ID_RE = re.compile(r"(?:^|&)list=([0-9A-Za-z_-]+)")
_CHANNEL_PATH_RE = re.compile(r"^/(@[\w.\-%]+|channel/[\w\-]+|c/[\w.\-%]+|user/[\w.\-%]+)(?:/([A-Za-z]+))?/?$")
_BAD_URL_CHARS_RE = re.compile(r"[\s\x00-\x1f\\]")
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024  # caption downloads and caption files bigger than this are refused
# per-video problems that need a login (we never log in) -> skip that video, suggest a caption file
NEEDS_LOGIN_RE = re.compile(r"confirm your age|age.restricted|inappropriate for some users|members.only|"
                            r"join this channel|private video|video unavailable", re.I)
# connection-wide blocks -> stop everything immediately
BLOCKED_RE = re.compile(r"confirm you.{0,3}re not a bot|sign in to confirm|too many requests|\b429\b|"
                        r"rate.?limit", re.I)
KEY_RE = re.compile(r"API key not valid|API_KEY_INVALID|API key expired|PERMISSION_DENIED|UNAUTHENTICATED", re.I)
RETRYABLE_RE = re.compile(r"\b(429|500|502|503|504)\b|UNAVAILABLE|RESOURCE_EXHAUSTED|overloaded|timed? ?out", re.I)
CAPTION_FILE_EXTS = (".vtt", ".srt", ".sbv", ".json3", ".json")


def classify_url(line: str):
    """Return (kind, value). kind: video | playlist | channel | unknown | skip.

    Safety: only YouTube hosts are accepted, and the value returned is rebuilt from scratch
    (video id / playlist id / channel path on www.youtube.com). The pasted text itself is never passed on,
    so a crafted link can't point yt-dlp at another website.
    """
    s = line.strip()
    if not s or s.startswith("#"):
        return "skip", None
    if BARE_ID_RE.fullmatch(s):
        return "video", s
    if not re.match(r"https?://", s, re.I):
        if re.match(r"(www\.|m\.)?(youtube\.com|youtu\.be)/", s, re.I):
            s = "https://" + s
        else:
            return "unknown", s
    if _BAD_URL_CHARS_RE.search(s):
        return "unknown", s
    try:
        parts = urllib.parse.urlsplit(s)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return "unknown", s
    if host not in YT_HOSTS or "@" in parts.netloc:
        return "unknown", s
    m = VIDEO_ID_RE.search(s)
    if m:
        return "video", m.group(1)  # a watch URL that also has &list= is still just that video
    lm = _LIST_ID_RE.search(parts.query)
    if lm:
        return "playlist", f"{YT_BASE}/playlist?list={lm.group(1)}"
    cm = _CHANNEL_PATH_RE.match(parts.path)
    if cm:
        tab = (cm.group(2) or "").lower()
        return "channel", f"{YT_BASE}/{cm.group(1)}" + (f"/{tab}" if tab in CHANNEL_TABS else "")
    return "unknown", s


def normalize_channel_url(url: str, tab: str) -> str:
    parts = urllib.parse.urlsplit(url)
    path = parts.path.rstrip("/")
    last = path.rsplit("/", 1)[-1].lower()
    if last not in CHANNEL_TABS:
        path = f"{path}/{tab}"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


_WIN_RESERVED_RE = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.I)


def safe_filename(title: str, limit: int = 120) -> str:
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title or "")
    t = re.sub(r"\s+", " ", t).strip()
    t = t[:limit].rstrip(" .")
    if _WIN_RESERVED_RE.match(t):  # Windows device names (CON, NUL, COM1 ...) can't be used as file names
        t = "_" + t
    return t or "untitled"


def redact(text, secret: str) -> str:
    """Remove a secret (the API key) from text before it is shown or logged."""
    text = str(text)
    return text.replace(secret, "[key hidden]") if secret and len(secret) >= 8 else text


def fmt_ts(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def parse_json3(raw: str) -> list[str]:
    out = []
    for ev in json.loads(raw).get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text:
            continue
        out.append(f"[{fmt_ts(int(ev.get('tStartMs', 0) / 1000))}] {text}")
    return out


_CUE_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,]\d{1,3}\s*-->")
_SBV_TIME = re.compile(r"^(\d+):(\d{1,2}):(\d{2})\.\d{1,3}\s*,\s*\d+:\d{1,2}:\d{2}\.\d{1,3}\s*$")
_TAG = re.compile(r"<[^>]+>|\{\\[^}]*\}")


def _cue_lines(raw: str, time_re, hms) -> list[str]:
    out, ts, last = [], "00:00:00", None
    for line in raw.lstrip("﻿").splitlines():
        line = line.strip()
        m = time_re.match(line)
        if m:
            ts = fmt_ts(hms(m))
            continue
        if not line or line.startswith(("WEBVTT", "NOTE", "Kind:", "Language:", "STYLE", "REGION")):
            continue
        if line.isdigit():  # SRT cue number
            continue
        text = _TAG.sub("", line).strip()
        if not text or text == last:  # auto-captions repeat lines
            continue
        last = text
        out.append(f"[{ts}] {text}")
    return out


def parse_vtt(raw: str) -> list[str]:
    """WebVTT and SRT."""
    return _cue_lines(raw, _CUE_TIME, lambda m: int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3)))


def parse_sbv(raw: str) -> list[str]:
    return _cue_lines(raw, _SBV_TIME, lambda m: int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)))


def parse_caption_text(raw: str, ext: str = "") -> list[str]:
    """Parse any supported caption format; uses the extension as a hint, then sniffs."""
    ext = ext.lower().lstrip(".")
    s = raw.lstrip("﻿").lstrip()
    if ext in ("json3", "json") or s.startswith("{"):
        try:
            return parse_json3(s)
        except ValueError:
            return []
    if ext == "sbv" or (_SBV_TIME.match(s.splitlines()[0].strip()) if s else False):
        return parse_sbv(s)
    return parse_vtt(s)


def pick_caption(info: dict, lang: str):
    """Best caption track as (url, ext, language_key, kind) or None.

    Never returns a YouTube machine-*translated* track (URL has tlang=): those are known to trigger
    HTTP 429 rate limiting. Order: creator-uploaded captions in `lang` -> original-language auto captions.
    """

    def usable(tracks):
        for ext in ("json3", "vtt"):
            for t in tracks or []:
                if t.get("ext") == ext and t.get("url"):
                    return t["url"], ext
        return None

    subs = info.get("subtitles") or {}
    for k in [lang] + sorted(k for k in subs if k.startswith(lang + "-")):
        r = usable(subs.get(k))
        if r:
            return r[0], r[1], k, "manual"

    auto = info.get("automatic_captions") or {}
    order = [f"{lang}-orig", lang] + sorted(k for k in auto if k.endswith("-orig")) + sorted(auto)
    for k in dict.fromkeys(order):
        tracks = [t for t in auto.get(k) or [] if "tlang=" not in (t.get("url") or "")]
        r = usable(tracks)
        if r:
            return r[0], r[1], k, "auto"
    return None


def build_system_prompt(prompt: str, min_segment_minutes: float) -> str:
    n = int(min_segment_minutes) if float(min_segment_minutes).is_integer() else min_segment_minutes
    return prompt.replace("{min_minutes}", str(n)).strip()


def find_existing_output(output_dir: str, video_id: str) -> Optional[str]:
    try:
        for name in os.listdir(output_dir):
            if name.endswith(f"[{video_id}].txt"):
                return name
    except OSError:
        pass
    return None


def check_writable(folder: str) -> Optional[str]:
    """Create the folder if needed and test that files can be saved in it. Returns None if fine, else a
    plain-language problem. Done before any work so no Gemini calls are wasted on results that can't be saved."""
    try:
        os.makedirs(folder, exist_ok=True)
        probe = Path(folder, f".write-test-{os.getpid()}.tmp")
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return None
    except PermissionError:
        return (f"Windows won't let this app save into \"{folder}\". If Windows Security's 'Controlled folder "
                "access' is on, it blocks apps it doesn't know from folders like Documents, Desktop and Pictures. "
                "Either choose a different folder in Settings, or allow the app: Windows Security → Virus & threat "
                "protection → Manage ransomware protection → Allow an app through Controlled folder access. "
                "(Or click 'Allow on device' on Windows' pop-up.)")
    except OSError as e:
        return f"Can't save into \"{folder}\": {e}"


def unique_path(folder: str, stem: str, suffix: str = ".txt") -> Path:
    p = Path(folder, stem + suffix)
    n = 2
    while p.exists():
        p = Path(folder, f"{stem} ({n}){suffix}")
        n += 1
    return p


RESULT_SUFFIX_RE = re.compile(r"\s*\[[0-9A-Za-z_-]{11}\]\.txt$")


def list_results(output_dir: str, limit: int = 300) -> list[tuple[float, str]]:
    """(mtime, path) of saved result files, newest first. Raw transcripts are left out."""
    out = []
    try:
        for e in os.scandir(output_dir):
            n = e.name
            if e.is_file() and n.lower().endswith(".txt") and not n.lower().endswith(".transcript.txt"):
                out.append((e.stat().st_mtime, e.path))
    except OSError:
        return []
    out.sort(reverse=True)
    return out[:limit]


def result_title(path: str) -> str:
    name = os.path.basename(path)
    t = RESULT_SUFFIX_RE.sub("", name)
    return t[:-4] if t.lower().endswith(".txt") else t


def find_deno() -> Optional[str]:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        for cand in (base / "deno.exe", base / "deno", Path(sys.executable).parent / "deno.exe"):
            if cand.exists():
                return str(cand)
    return shutil.which("deno")


_CAPTION_HOSTS = ("youtube.com", "google.com", "googlevideo.com")


class _Refused(RuntimeError):
    """A download was refused on safety grounds (never retried)."""


def _caption_url_ok(url: str) -> bool:
    """Caption downloads must be https and on a YouTube/Google host (blocks file://, http://, other sites)."""
    try:
        p = urllib.parse.urlsplit(url)
        host = (p.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return (p.scheme == "https" and "@" not in p.netloc and not _BAD_URL_CHARS_RE.search(url)
            and any(host == h or host.endswith("." + h) for h in _CAPTION_HOSTS))


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _caption_url_ok(newurl):
            raise _Refused("caption download was redirected to an unexpected address; refusing")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_text(url: str, retries: int = 3, stop: Optional[threading.Event] = None) -> str:
    """Plain, logged-out download. A 429 is raised at once as YouTubeBlocked (never retried)."""
    if not _caption_url_ok(url):
        raise _Refused("caption address isn't a secure YouTube address; refusing to download it")
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    opener = urllib.request.build_opener(_SafeRedirects)
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with opener.open(req, timeout=60) as r:
                data = r.read(MAX_DOWNLOAD_BYTES + 1)
            if len(data) > MAX_DOWNLOAD_BYTES:
                raise _Refused("caption download is unexpectedly large; refusing")
            return data.decode("utf-8", errors="replace")
        except _Refused:
            raise
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise YouTubeBlocked("YouTube answered 429 Too Many Requests for captions.")
            if e.code in (401, 403, 404):
                raise RuntimeError(f"caption download refused (HTTP {e.code})")
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        wait = attempt * 5
        if stop is not None:
            if stop.wait(wait):
                raise StopRequested()
        else:
            time.sleep(wait)
    raise RuntimeError(f"caption download failed: {last}")


def generate(api_key: str, model: str, system: str, transcript: str) -> str:
    """One Gemini call. Raises the raw SDK exception on failure."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=model,
        contents=f"Transcript:\n{transcript}",
        config=types.GenerateContentConfig(system_instruction=system, temperature=0.1),
    )
    return resp.text or ""


def test_api_key(api_key: str, model: str) -> tuple[bool, str]:
    try:
        text = generate(api_key, model, "Reply with the single word: ok", "ping")
        return True, f"Works. Model replied: {text.strip()[:40] or '(empty)'}"
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if KEY_RE.search(msg):
            return False, "Gemini rejected this API key."
        if "404" in msg or "NOT_FOUND" in msg:
            return False, f"Model '{model}' was not found. Pick another model."
        return False, f"Failed: {redact(msg, api_key)[:200]}"


# Always logged out, never reads a yt-dlp config file, fetches only what's needed for captions,
# and only YouTube extractors are allowed (yt-dlp can't be pointed at any other website).
SAFE_YTDLP_ARGS = [
    "--ignore-config", "--no-cookies", "--no-cookies-from-browser",
    "--use-extractors", "youtube.*",
    "--skip-download", "--ignore-no-formats-error", "--no-playlist",
    "--sleep-requests", "1",
    "--extractor-args", "youtube:skip=hls,dash,translated_subs",
]


def build_ydl_opts(extra: list[str], deno_path: Optional[str], logger) -> dict:
    """Build yt-dlp options by parsing CLI-style args (most stable way to set the JS-runtime options)."""
    import yt_dlp

    base = SAFE_YTDLP_ARGS + extra
    full = list(base)
    if deno_path:
        full += ["--js-runtimes", f"deno:{deno_path}"]
    # No --remote-components: the app ships yt-dlp's own challenge-solver package (yt-dlp-ejs), so nothing
    # is downloaded and run at runtime. (Falls back to `base` if this yt-dlp doesn't know --js-runtimes.)
    attempts = [full, base]
    last = None
    for args in attempts:
        try:
            opts = dict(yt_dlp.parse_options(args).ydl_opts)
        except (SystemExit, Exception) as e:  # noqa: BLE001  (argparse exits on unknown flags)
            last = e
            continue
        # belt and braces: no login can sneak in
        opts.pop("cookiefile", None)
        opts.pop("cookiesfrombrowser", None)
        opts.pop("username", None)
        opts.pop("password", None)
        opts.pop("usenetrc", None)
        opts["logger"] = logger
        return opts
    raise RuntimeError(f"Could not set up yt-dlp: {last}")


class _YtLogger:
    def __init__(self, log):
        self.log = log

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        if re.search(r"javascript runtime|ejs", msg, re.I):
            self.log(f"yt-dlp: {msg}", "warn")

    def error(self, msg):
        pass  # the raised DownloadError carries the same text


def _flatten(info: dict) -> list[dict]:
    out = []
    for e in info.get("entries") or []:
        if not e:
            continue
        if e.get("entries"):
            out += _flatten(e)
        elif e.get("id") and len(str(e["id"])) == 11:
            out.append(e)
    return out


def _last_line(msg: str) -> str:
    lines = [x for x in str(msg).splitlines() if x.strip()]
    return (lines[-1] if lines else str(msg))[:200]


# --------------------------------------------------------------------------- runner


class Runner:
    def __init__(self, cfg: RunConfig, urls_text: str, log: Callable, progress: Callable, status: Callable,
                 stop: threading.Event, files: Optional[list[str]] = None):
        self.cfg, self.urls_text = cfg, urls_text or ""
        self.files = list(files or [])
        # every message passes through redact(), so the API key can never end up in the activity log
        self.log = lambda msg, kind="info": log(redact(msg, cfg.api_key), kind)
        self.progress, self.status, self.stop = progress, status, stop
        self.saved: list[str] = []
        self.needs_file: list[str] = []  # videos that can't be read logged out (age-restricted etc.)
        self._deno = find_deno()

    # ---- plumbing
    def _check_stop(self):
        if self.stop.is_set():
            raise StopRequested()

    def _sleep(self, seconds: float):
        if self.stop.wait(seconds):
            raise StopRequested()

    def _opts(self, extra: list[str]) -> dict:
        return build_ydl_opts(extra, self._deno, _YtLogger(self.log))

    def _validate(self):
        c = self.cfg
        if not c.api_key:
            raise RuntimeError("Enter your Gemini API key in Settings.")
        if not c.model:
            raise RuntimeError("Pick a Gemini model in Settings.")
        if not c.prompt.strip():
            raise RuntimeError("The prompt is empty. Pick a preset on the Prompt page.")
        if "<<" in c.prompt:
            raise RuntimeError("The prompt still has <<...>> placeholders. Fill them in on the Prompt page.")
        if not c.output_dir:
            raise RuntimeError("Choose an output folder in Settings.")
        if c.delay_min > c.delay_max:
            c.delay_min, c.delay_max = c.delay_max, c.delay_min
        if not self.urls_text.strip() and not self.files:
            raise RuntimeError("Paste at least one YouTube link or add a caption file first.")

    # ---- yt-dlp
    def _fetch_info(self, url: str) -> dict:
        import yt_dlp

        with yt_dlp.YoutubeDL(self._opts([])) as ydl:
            return ydl.extract_info(url, download=False)

    def _list_entries(self, url: str) -> list[dict]:
        import yt_dlp

        extra = ["--flat-playlist", "--yes-playlist"]
        if self.cfg.max_videos > 0:
            extra += ["--playlist-end", str(self.cfg.max_videos)]
        opts = self._opts(extra)
        opts["noplaylist"] = False
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = _flatten(info or {})
        return entries[: self.cfg.max_videos] if self.cfg.max_videos > 0 else entries

    def _expand(self) -> list[tuple[str, str, str]]:
        """-> [(kind, key, target)]: ("file", path, path) or ("video", video_id, url)."""
        items, seen = [], set()
        for f in self.files:
            items.append(("file", f, f))

        def add(vid):
            if vid not in seen:
                seen.add(vid)
                items.append(("video", vid, f"https://www.youtube.com/watch?v={vid}"))

        for raw in self.urls_text.splitlines():
            self._check_stop()
            kind, val = classify_url(raw)
            if kind == "skip":
                continue
            if kind == "video":
                add(val)
            elif kind in ("playlist", "channel"):
                url = normalize_channel_url(val, self.cfg.channel_tab) if kind == "channel" else val
                limit = self.cfg.max_videos or "all"
                self.status(f"Listing videos from {url}")
                self.log(f"Listing newest {limit} videos from {url} ...")
                try:
                    entries = self._list_entries(url)
                except Exception as e:  # noqa: BLE001
                    if BLOCKED_RE.search(str(e)):
                        raise YouTubeBlocked(str(e))
                    self.log(f"Could not list {url}: {_last_line(e)}", "error")
                    continue
                self.log(f"  found {len(entries)} videos")
                for e in entries:
                    add(e["id"])
                self._sleep(random.uniform(3, 6))
            else:
                self.log(f"Not a YouTube link, ignoring: {raw.strip()[:80]}", "warn")
        return items

    # ---- shared tail: transcript -> Gemini -> file
    def _finish(self, lines: list[str], title: str, out_path: Path) -> str:
        transcript = "\n".join(lines)
        if self.cfg.save_transcript:
            out_path.with_suffix(".transcript.txt").write_text(transcript, encoding="utf-8")
        self.status(f"Asking Gemini: {title[:60]}")
        self.log(f"Sending {len(lines)} caption lines to {self.cfg.model} ...")
        text = self._ask_gemini(transcript)
        if not text.strip():
            self.log(f"{title[:60]}: Gemini returned an empty answer (possibly blocked). Nothing saved.", "error")
            return "failed"
        out_path.write_text(text, encoding="utf-8")
        self.saved.append(str(out_path))
        self.log(f"Saved: {out_path.name}", "ok")
        return "done"

    # ---- one caption file
    def _process_file(self, path: str) -> str:
        p = Path(path)
        self.log(f"Reading caption file {p.name} ...")
        try:
            if p.stat().st_size > MAX_DOWNLOAD_BYTES:
                self.log(f"{p.name}: file is too large to be a caption file (over "
                         f"{MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB).", "error")
                return "failed"
            raw = p.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as e:
            self.log(f"{p.name}: couldn't read it: {e}", "error")
            return "failed"
        lines = parse_caption_text(raw, p.suffix)
        if not lines:
            self.log(f"{p.name}: no captions found in this file. Is it a .vtt, .srt or .sbv caption file?", "error")
            return "failed"
        title = p.stem
        out = unique_path(self.cfg.output_dir, safe_filename(title))
        return self._finish(lines, title, out)

    # ---- one video
    def _process_video(self, vid: str, url: str) -> tuple[str, bool]:
        """Return (status, touched_youtube). status: done | skipped | failed."""
        c = self.cfg
        if c.skip_existing:
            ex = find_existing_output(c.output_dir, vid)
            if ex:
                self.log(f"Skipping {vid}: already have '{ex}'")
                return "skipped", False

        self.log(f"Fetching video details for {vid} ...")
        try:
            info = self._fetch_info(url)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if NEEDS_LOGIN_RE.search(msg):
                self.needs_file.append(vid)
                self.log(f"{vid}: YouTube only shows this video to logged-in viewers ({_last_line(msg)}). "
                         "Use a caption file for it instead.", "error")
                return "failed", True
            if BLOCKED_RE.search(msg):
                raise YouTubeBlocked(msg)
            self.log(f"{vid}: could not get video info: {_last_line(msg)}", "error")
            return "failed", True

        title = info.get("title") or vid
        self.log(f"Title: {title}")
        live = info.get("live_status")
        if live in ("is_live", "is_upcoming"):
            self.log(f"Skipping {vid}: stream is not finished yet ({live}).", "warn")
            return "skipped", True
        dur = info.get("duration") or 0
        if dur and dur < c.min_stream_minutes * 60:
            self.log(f"Skipping {vid}: {dur / 60:.1f} min is shorter than the {c.min_stream_minutes:g} min minimum.")
            return "skipped", True

        cap = pick_caption(info, c.caption_lang)
        if not cap:
            self.log(f"{vid}: no captions available yet. YouTube can take a while after a stream ends; "
                     "try again later or use a caption file.", "error")
            return "failed", True
        sub_url, ext, key, kind = cap
        if not key.split("-")[0] == c.caption_lang:
            self.log(f"No '{c.caption_lang}' captions; using the stream's original-language captions ({key}).",
                     "warn")

        self._sleep(random.uniform(2, 5))  # don't fire the caption request right on the heels of the page fetch
        self.status(f"Downloading captions: {title[:60]}")
        try:
            raw = download_text(sub_url, stop=self.stop)
            lines = parse_json3(raw) if ext == "json3" else parse_vtt(raw)
        except (StopRequested, YouTubeBlocked):
            raise
        except Exception as e:  # noqa: BLE001
            self.log(f"{vid}: caption problem: {e}", "error")
            return "failed", True
        if not lines:
            self.log(f"{vid}: captions were empty.", "error")
            return "failed", True
        return self._finish(lines, title, Path(c.output_dir, f"{safe_filename(title)} [{vid}].txt")), True

    def _ask_gemini(self, transcript: str) -> str:
        system = build_system_prompt(self.cfg.prompt, self.cfg.min_segment_minutes)
        for attempt in range(1, self.cfg.max_retries + 1):
            self._check_stop()
            try:
                return generate(self.cfg.api_key, self.cfg.model, system, transcript)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if KEY_RE.search(msg):
                    raise ApiKeyProblem(msg)
                if RETRYABLE_RE.search(msg) and attempt < self.cfg.max_retries:
                    wait = attempt * (20 if "429" in msg or "RESOURCE_EXHAUSTED" in msg else 10)
                    self.log(f"Gemini busy (attempt {attempt}/{self.cfg.max_retries}). Retrying in {wait}s ...", "warn")
                    self._sleep(wait)
                    continue
                self.log(f"Gemini error: {msg[:300]}", "error")
                return ""
        return ""

    # ---- main loop
    def run(self) -> dict:
        summary = {"done": 0, "skipped": 0, "failed": 0, "stopped": False, "blocked": False,
                   "api_key_problem": False, "error": "", "saved": [], "needs_file": [], "capped": 0}
        try:
            self._validate()
            problem = check_writable(self.cfg.output_dir)
            if problem:
                raise RuntimeError(problem)
            if self.urls_text.strip() and not self._deno:
                self.log("Deno was not found. YouTube may refuse to give captions without it.", "warn")
            items = self._expand()
            videos = [x for x in items if x[0] == "video"]
            cap = self.cfg.max_per_run
            if cap > 0 and len(videos) > cap:
                dropped = {x[1] for x in videos[cap:]}
                items = [x for x in items if not (x[0] == "video" and x[1] in dropped)]
                summary["capped"] = len(dropped)
                self.log(f"Only the first {cap} videos will be done this run (Settings → Videos per run). "
                         f"Run the rest later; spreading runs out keeps YouTube from limiting you.", "warn")
            total = len(items)
            self.log(f"{total} item(s) to check.")
            self.progress(0, total)
            for i, (kind, key, target) in enumerate(items, 1):
                self._check_stop()
                self.status(f"Item {i} of {total}")
                self.log(f"--- {i}/{total}: {Path(key).name if kind == 'file' else key} ---")
                touched = False
                try:
                    if kind == "file":
                        status = self._process_file(target)
                    else:
                        status, touched = self._process_video(key, target)
                except (StopRequested, YouTubeBlocked, ApiKeyProblem):
                    raise
                except Exception as e:  # noqa: BLE001
                    self.log(f"{key}: unexpected error: {e}", "error")
                    status, touched = "failed", kind == "video"
                summary[status] += 1
                self.progress(i, total)
                more_videos = any(k == "video" for k, _, _ in items[i:])
                if touched and more_videos:
                    d = random.uniform(self.cfg.delay_min, self.cfg.delay_max)
                    self.status(f"Waiting {d:.0f}s before the next video")
                    self._sleep(d)
        except StopRequested:
            summary["stopped"] = True
            self.log("Stopped.", "warn")
        except YouTubeBlocked as e:
            summary["blocked"] = True
            self.log("YouTube is limiting requests from this internet connection, so the run was stopped to "
                     f"avoid making it worse. ({_last_line(e)})", "error")
        except ApiKeyProblem:
            summary["api_key_problem"] = True
            self.log("Gemini rejected the API key.", "error")
        except Exception as e:  # noqa: BLE001
            summary["error"] = redact(e, self.cfg.api_key)
            self.log(str(e), "error")
        finally:
            summary["saved"] = list(self.saved)
            summary["needs_file"] = list(self.needs_file)
        return summary
