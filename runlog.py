"""Saves each run's Activity log to a text file, so a problem can still be looked at after the app is closed.

Files go to the app's settings folder (%APPDATA%\\StreamTimestamper\\logs), one per run, named
run-YYYY-MM-DD_HH-MM-SS.txt. Only the newest KEEP files are kept. Lines are written exactly as shown in the
Activity box, with the API key removed. Logging problems (full disk, no permission) never stop a run.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, Optional

KEEP = 10
PREFIX = "run-"


def log_dir() -> Path:
    import settings as store

    p = store.app_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


class RunLog:
    def __init__(self, folder: Optional[Path] = None, keep: int = KEEP):
        self.folder = folder
        self.keep = max(1, keep)
        self.fh = None
        self.path: Optional[Path] = None

    def start(self, header: Iterable[str] = ()) -> None:
        self.close()
        try:
            folder = self.folder or log_dir()
            folder.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
            path = folder / f"{PREFIX}{stamp}.txt"
            n = 2
            while path.exists():
                path = folder / f"{PREFIX}{stamp}_{n}.txt"
                n += 1
            self.fh = open(path, "w", encoding="utf-8")
            self.path = path
        except OSError:
            self.fh, self.path = None, None
            return
        for line in header:
            self.write(line)
        self._prune(folder)

    def write(self, line: str) -> None:
        if self.fh is None:
            return
        try:
            self.fh.write(str(line).rstrip("\n") + "\n")
            self.fh.flush()
        except (OSError, ValueError):
            self.close()

    def close(self) -> None:
        fh, self.fh = self.fh, None
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass

    def _prune(self, folder: Path) -> None:
        try:
            files = sorted((p for p in folder.glob(f"{PREFIX}*.txt") if p.is_file()), key=lambda p: p.name,
                           reverse=True)
        except OSError:
            return
        for old in files[self.keep:]:
            if old != self.path:
                try:
                    old.unlink()
                except OSError:
                    pass
