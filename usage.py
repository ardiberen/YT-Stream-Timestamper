"""Counts the Gemini requests this app sends, per model and per day.

Free Gemini keys get a daily number of requests per model, and the count resets at midnight Pacific time, so the
counter uses the Pacific date. It only knows about requests from this app on this PC (not other apps using the
same key), and Google doesn't say how many are left, so it shows what was used, not what remains. Stored in
settings.json under "gemini_usage": {"date": "YYYY-MM-DD", "counts": {model: n}, "out": [models at their limit]}.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional


def _nth_sunday(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)  # first Sunday
    return d + timedelta(weeks=n - 1)


def pacific_today(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        return now.astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
    except Exception:  # noqa: BLE001  (no time-zone data on this PC: use the US daylight-saving rules)
        utc = now.astimezone(timezone.utc)
        y = utc.year
        start = datetime.combine(_nth_sunday(y, 3, 2), datetime.min.time(), timezone.utc) + timedelta(hours=10)
        end = datetime.combine(_nth_sunday(y, 11, 1), datetime.min.time(), timezone.utc) + timedelta(hours=9)
        offset = -7 if start <= utc < end else -8
        return (utc + timedelta(hours=offset)).date().isoformat()


def _today(s: dict, now: Optional[datetime] = None) -> dict:
    day = pacific_today(now)
    u = s.get("gemini_usage")
    if not isinstance(u, dict) or u.get("date") != day:
        u = {"date": day, "counts": {}, "out": []}
    counts = u.get("counts") if isinstance(u.get("counts"), dict) else {}
    u["counts"] = {str(k): int(v) for k, v in counts.items() if isinstance(v, int) and v > 0}
    u["out"] = [str(m) for m in u.get("out") or [] if isinstance(m, str)]
    s["gemini_usage"] = u
    return u


def record(s: dict, model: str, now: Optional[datetime] = None) -> None:
    """One request was sent to `model`."""
    if not model:
        return
    u = _today(s, now)
    u["counts"][model] = u["counts"].get(model, 0) + 1


def mark_out(s: dict, model: str, now: Optional[datetime] = None) -> None:
    """Google said `model` has used up today's requests."""
    u = _today(s, now)
    if model and model not in u["out"]:
        u["out"].append(model)


def describe(s: dict, now: Optional[datetime] = None) -> str:
    u = _today(s, now)
    if not u["counts"] and not u["out"]:
        return "Gemini requests today: none yet"
    parts = []
    for m in sorted(set(u["counts"]) | set(u["out"]), key=lambda m: -u["counts"].get(m, 0)):
        parts.append(f"{m} {u['counts'].get(m, 0)}" + (" (limit reached)" if m in u["out"] else ""))
    return "Gemini requests today: " + ", ".join(parts)
