"""Turn a recording on this PC (video or audio file) into a caption file with Whisper. No GUI code.

* Runs on this PC with faster-whisper (no YouTube contact, no Gemini requests).
* The recording is decoded and transcribed 10 minutes at a time, so memory stays small even for a 7-hour stream.
* Each finished piece is written to "<name>.srt.partial" straight away, with a small progress file next to it. If
  the app is closed (or Stop is pressed), the next try continues from the last finished piece.
* When it's done, the partial file becomes "<name>.srt": a normal caption file the app (or anything else) reads.
* The speech model is downloaded from Hugging Face the first time a model size is used (the app asks first) and
  kept in the app's settings folder. Nothing else is downloaded.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Callable, Iterator, Optional

SAMPLE_RATE = 16000
CHUNK_SECONDS = 600
MODELS = {  # name -> (approximate download size, short description)
    "base": ("about 140 MB", "fast: about 20 min for a 7-hour stream on a typical PC"),
    "small": ("about 460 MB", "more accurate, about twice as slow"),
}
DEFAULT_MODEL = "base"
MEDIA_EXTS = (".mp4", ".mkv", ".mov", ".flv", ".webm", ".ts", ".m4a", ".mp3", ".wav", ".aac", ".ogg", ".opus",
              ".flac", ".wma", ".avi")


class Stopped(Exception):
    """Stop was pressed; the work so far is kept and continues next time."""


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def models_dir() -> Path:
    import settings as store

    p = store.app_dir() / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def model_downloaded(name: str, folder: Optional[Path] = None) -> bool:
    try:
        from faster_whisper.utils import download_model

        download_model(name, local_files_only=True, cache_dir=str(folder or models_dir()))
        return True
    except Exception:  # noqa: BLE001
        return False


def _quiet_hub() -> None:
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")


def load_model(name: str, folder: Optional[Path] = None):
    _quiet_hub()
    from faster_whisper import WhisperModel

    return WhisperModel(name, device="cpu", compute_type="int8", download_root=str(folder or models_dir()))


# ---------------------------------------------------------------- output files
def output_paths(recording: str, out_dir: str) -> tuple[Path, Path, Path]:
    """(final .srt, .srt.partial, .progress.json) for a recording."""
    stem = Path(recording).stem
    folder = Path(out_dir, "Transcripts")
    return folder / f"{stem}.srt", folder / f"{stem}.srt.partial", folder / f"{stem}.progress.json"


def _source_id(recording: str) -> dict:
    st = os.stat(recording)
    return {"source": os.path.abspath(recording), "size": st.st_size, "mtime": int(st.st_mtime)}


def resume_point(recording: str, out_dir: str) -> float:
    """Seconds already transcribed for this exact file (0 = start fresh)."""
    _, partial, progress = output_paths(recording, out_dir)
    try:
        info = json.loads(progress.read_text(encoding="utf-8"))
        same = {k: info.get(k) for k in ("source", "size", "mtime")} == _source_id(recording)
        if same and partial.exists():
            return float(info.get("done_until", 0))
    except (OSError, ValueError):
        pass
    return 0.0


def discard_partial(recording: str, out_dir: str) -> None:
    """Delete an unfinished transcript of `recording` (Stop + 'delete the unfinished parts')."""
    _, partial, progress = output_paths(recording, out_dir)
    for p in (partial, progress):
        try:
            p.unlink()
        except OSError:
            pass


def unfinished(out_dir: str) -> list[dict]:
    """Interrupted transcriptions in this output folder: [{"source", "done_until", "duration"}]."""
    out = []
    folder = Path(out_dir, "Transcripts")
    try:
        files = list(folder.glob("*.progress.json"))
    except OSError:
        return out
    for p in files:
        try:
            info = json.loads(p.read_text(encoding="utf-8"))
            if os.path.isfile(info.get("source", "")):
                out.append(info)
        except (OSError, ValueError):
            pass
    return out


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _cue_count(partial: Path) -> int:
    try:
        return sum(1 for line in partial.read_text(encoding="utf-8").splitlines() if "-->" in line)
    except OSError:
        return 0


# ---------------------------------------------------------------- decoding
def media_duration(recording: str) -> Optional[float]:
    import av

    with av.open(recording, metadata_errors="ignore") as c:
        return float(c.duration / av.time_base) if c.duration else None


def iter_chunks(recording: str, start: float = 0.0, chunk_seconds: int = 0) -> Iterator:
    """Yield (chunk start seconds, float32 mono 16 kHz samples), CHUNK_SECONDS at a time, from `start`."""
    import av
    import numpy as np

    chunk_seconds = chunk_seconds or CHUNK_SECONDS
    need = chunk_seconds * SAMPLE_RATE
    with av.open(recording, metadata_errors="ignore") as c:
        streams = [s for s in c.streams if s.type == "audio"]
        if not streams:
            raise ValueError("This file has no sound track.")
        stream = streams[0]
        if start > 0:
            c.seek(int(start * av.time_base), backward=True, any_frame=False)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        buf: list = []
        have = 0
        at = start

        def take(frames):
            nonlocal have
            for rf in frames or []:
                arr = rf.to_ndarray().reshape(-1)
                buf.append(arr)
                have += len(arr)

        for frame in c.decode(stream):
            t = frame.time
            if t is not None and start > 0 and t + frame.samples / max(1, frame.sample_rate) <= start:
                continue  # before the resume point (the seek lands on the keyframe before it)
            take(resampler.resample(frame))
            while have >= need:
                allarr = np.concatenate(buf)
                yield at, allarr[:need].astype(np.float32) / 32768.0
                rest = allarr[need:]
                buf[:] = [rest]
                have = len(rest)
                at += chunk_seconds
        take(resampler.resample(None))
        if have:
            yield at, np.concatenate(buf).astype(np.float32) / 32768.0


# ---------------------------------------------------------------- the job
def transcribe(recording: str, out_dir: str, model=None, model_name: str = DEFAULT_MODEL, language: str = "",
               progress: Callable[[float, Optional[float]], None] = lambda done, total: None,
               stop: Optional[threading.Event] = None, log: Callable[[str], None] = lambda m: None) -> Path:
    """Transcribe (or continue transcribing) `recording`. Returns the finished .srt path.

    Raises Stopped if `stop` is set; everything finished so far is kept for next time."""
    final, partial, progress_file = output_paths(recording, out_dir)
    final.parent.mkdir(parents=True, exist_ok=True)
    start = resume_point(recording, out_dir)
    if start <= 0:
        partial.write_text("", encoding="utf-8")
    else:
        log(f"Continuing from {_srt_time(start)[:8]} (the earlier part is already saved).")
    total = media_duration(recording)
    model = model or load_model(model_name)
    n = _cue_count(partial)
    progress(start, total)
    for at, samples in iter_chunks(recording, start):
        if stop is not None and stop.is_set():
            raise Stopped()
        segments, _info = model.transcribe(samples, language=language or None, vad_filter=True, beam_size=1,
                                           condition_on_previous_text=False)
        lines = []
        for seg in segments:
            if stop is not None and stop.is_set():
                raise Stopped()  # this piece isn't saved; it's redone next time
            text = seg.text.strip()
            if text:
                n += 1
                lines.append(f"{n}\n{_srt_time(at + seg.start)} --> {_srt_time(at + seg.end)}\n{text}\n")
        done = at + len(samples) / SAMPLE_RATE
        with open(partial, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + ("\n" if lines else ""))
        tmp = progress_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({**_source_id(recording), "done_until": done, "duration": total,
                                   "model": model_name}), encoding="utf-8")
        os.replace(tmp, progress_file)
        progress(done, total)
    os.replace(partial, final)
    try:
        progress_file.unlink()
    except OSError:
        pass
    return final


def selftest() -> list[str]:
    """Check that everything transcribing needs is really inside this copy of the app (used by the release
    build on the finished exe). No model download. Returns problems; empty = fine."""
    import math
    import struct
    import tempfile
    import wave

    problems = []
    try:
        import ctranslate2  # noqa: F401
        import numpy as np
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        with tempfile.TemporaryDirectory() as d:
            wav = Path(d, "tone.wav")
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 7))) for i in range(16000 * 3)))
            chunks = list(iter_chunks(str(wav), 0, 2))
            if [round(len(c) / SAMPLE_RATE, 1) for _, c in chunks] != [2.0, 1.0]:
                problems.append(f"audio decoding gave unexpected pieces: {[len(c) for _, c in chunks]}")
            get_speech_timestamps(np.concatenate([c for _, c in chunks]), VadOptions())  # loads the VAD model
    except Exception as e:  # noqa: BLE001
        problems.append(f"transcribing: {type(e).__name__}: {e}")
    return problems
