"""The Queue page's two lists, kept in settings.json. No GUI code, no network.

* Waiting for captions: streams that were still live, or had no captions yet, when the app tried them. While the
  app is open it re-checks each one about once an hour and gives up after WAIT_GIVE_UP_HOURS.
* Backlog: older streams from a channel or playlist, worked through a few per day (BACKLOG_PER_DAY by default) so
  free Gemini limits and YouTube's patience last. Waiting streams always go first.

settings["waiting"] = [{"id", "title", "added", "next_check", "checks"}]
settings["backlog"] = [{"url", "label", "pending": [{"id", "title"}], "done", "failed", "total", "added"}]
settings["auto_day"] = {"date": Pacific date, "backlog_done": n, "paused": bool}
"""
from __future__ import annotations

import time
from typing import Optional

import usage

CHECK_EVERY_SECONDS = 3600
WAIT_GIVE_UP_HOURS = 48
BACKLOG_PER_DAY = 5
FINISHED = ("done", "skipped", "failed")


def _now(now: Optional[float]) -> float:
    return time.time() if now is None else now


def _list(s: dict, key: str) -> list:
    v = s.get(key)
    if not isinstance(v, list):
        v = []
        s[key] = v
    return v


# ---------------------------------------------------------------- waiting for captions
def waiting(s: dict) -> list[dict]:
    return [w for w in _list(s, "waiting") if isinstance(w, dict) and w.get("id")]


def add_waiting(s: dict, vid: str, title: str = "", now: Optional[float] = None) -> bool:
    """Add a stream (or keep it if it's already there). True if it was new."""
    now = _now(now)
    for w in waiting(s):
        if w["id"] == vid:
            w["next_check"] = now + CHECK_EVERY_SECONDS
            w["checks"] = int(w.get("checks", 0)) + 1
            if title:
                w["title"] = title
            return False
    _list(s, "waiting").append({"id": vid, "title": title or vid, "added": now,
                                "next_check": now + CHECK_EVERY_SECONDS, "checks": 1})
    return True


def remove_waiting(s: dict, ids) -> None:
    ids = set(ids)
    s["waiting"] = [w for w in waiting(s) if w["id"] not in ids]


def expire_waiting(s: dict, now: Optional[float] = None) -> list[dict]:
    """Drop streams that have waited too long. Returns the dropped entries (for the log)."""
    cutoff = _now(now) - WAIT_GIVE_UP_HOURS * 3600
    old = [w for w in waiting(s) if float(w.get("added", 0)) < cutoff]
    remove_waiting(s, [w["id"] for w in old])
    return old


def due_waiting(s: dict, now: Optional[float] = None) -> list[str]:
    now = _now(now)
    return [w["id"] for w in waiting(s) if float(w.get("next_check", 0)) <= now]


def check_all_now(s: dict) -> None:
    for w in waiting(s):
        w["next_check"] = 0


def next_check_at(s: dict) -> Optional[float]:
    times = [float(w.get("next_check", 0)) for w in waiting(s)]
    return min(times) if times else None


# ---------------------------------------------------------------- backlog
def backlog(s: dict) -> list[dict]:
    return [b for b in _list(s, "backlog") if isinstance(b, dict) and b.get("url")]


def add_backlog(s: dict, url: str, label: str, videos: list[tuple[str, str]], skip_ids=(),
                now: Optional[float] = None) -> int:
    """Add a channel/playlist's videos [(id, title)]. Videos already queued anywhere, or in skip_ids (already
    done), are left out. Returns how many were added."""
    queued = {p["id"] for b in backlog(s) for p in b.get("pending", [])} | {w["id"] for w in waiting(s)}
    skip = set(skip_ids) | queued
    pending = [{"id": v, "title": t or v} for v, t in videos if v not in skip]
    for b in backlog(s):
        if b["url"] == url:  # adding the same channel again tops it up with anything new
            b["pending"] += pending
            b["total"] = int(b.get("total", 0)) + len(pending)
            return len(pending)
    _list(s, "backlog").append({"url": url, "label": label or url, "pending": pending, "done": 0, "failed": 0,
                                "total": len(pending), "added": _now(now)})
    return len(pending)


def remove_backlog(s: dict, url: str) -> None:
    s["backlog"] = [b for b in backlog(s) if b["url"] != url]


def _day(s: dict, now: Optional[float]) -> dict:
    from datetime import datetime, timezone

    today = usage.pacific_today(datetime.fromtimestamp(_now(now), timezone.utc))
    d = s.get("auto_day")
    if not isinstance(d, dict) or d.get("date") != today:
        d = {"date": today, "backlog_done": 0, "paused": False}
        s["auto_day"] = d
    return d


def per_day(s: dict) -> int:
    try:
        return max(0, int(s.get("backlog_per_day", BACKLOG_PER_DAY)))
    except (TypeError, ValueError):
        return BACKLOG_PER_DAY


def backlog_left_today(s: dict, now: Optional[float] = None) -> int:
    d = _day(s, now)
    return 0 if d["paused"] else max(0, per_day(s) - int(d.get("backlog_done", 0)))


def pause_today(s: dict, now: Optional[float] = None) -> None:
    """Gemini's daily limit was hit: no more automatic runs until midnight Pacific time."""
    _day(s, now)["paused"] = True


def paused_today(s: dict, now: Optional[float] = None) -> bool:
    return bool(_day(s, now)["paused"])


def next_backlog(s: dict, n: int) -> list[str]:
    out: list[str] = []
    for b in backlog(s):
        for p in b.get("pending", []):
            if len(out) >= n:
                return out
            out.append(p["id"])
    return out


# ---------------------------------------------------------------- planning and results
def plan_auto_run(s: dict, max_videos: int = 20, now: Optional[float] = None) -> list[str]:
    """Video ids for the next automatic run: due waiting streams first, then today's share of the backlog."""
    if paused_today(s, now):
        return []
    ids = due_waiting(s, now)[: max_videos or None]
    room = (max_videos - len(ids)) if max_videos else 10 ** 6
    if room > 0:
        ids += [v for v in next_backlog(s, min(room, backlog_left_today(s, now))) if v not in ids]
    return ids


def apply_outcomes(s: dict, outcomes: dict, waiting_titles: dict | None = None,
                   now: Optional[float] = None) -> None:
    """Update both lists from a run's {video id: done | skipped | failed | waiting}."""
    now = _now(now)
    waiting_titles = waiting_titles or {}
    waiting_ids = {w["id"] for w in waiting(s)}
    day = _day(s, now)
    for vid, status in outcomes.items():
        if status == "waiting":
            in_backlog = any(p["id"] == vid for b in backlog(s) for p in b.get("pending", []))
            if vid in waiting_ids or not in_backlog:
                add_waiting(s, vid, waiting_titles.get(vid, ""), now)
                continue
            status = "failed"  # an old backlog stream with no captions won't get them by waiting
        if status in FINISHED:
            if vid in waiting_ids:
                remove_waiting(s, [vid])
            for b in backlog(s):
                before = len(b.get("pending", []))
                b["pending"] = [p for p in b.get("pending", []) if p["id"] != vid]
                if len(b["pending"]) < before:
                    b["failed" if status == "failed" else "done"] = int(b.get(
                        "failed" if status == "failed" else "done", 0)) + 1
                    day["backlog_done"] = int(day.get("backlog_done", 0)) + 1
