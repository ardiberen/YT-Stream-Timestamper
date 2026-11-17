"""Transcription plumbing (pieces, saving as it goes, resuming) with a fake Whisper model.

Needs PyAV and NumPy (installed with faster-whisper); skipped without them. No model download, no network."""
import math
import os
import struct
import sys
import tempfile
import threading
import unittest
import wave
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core  # noqa: E402
import transcribe as tr  # noqa: E402

try:
    import av  # noqa: F401
    import numpy  # noqa: F401
    HAVE_AV = True
except ImportError:
    HAVE_AV = False

Seg = namedtuple("Seg", "start end text")


class FakeModel:
    """Says one line every 10 s of each piece it's given."""

    def __init__(self):
        self.calls = 0

    def transcribe(self, samples, **kw):
        self.calls += 1
        secs = len(samples) / tr.SAMPLE_RATE
        return iter([Seg(t, t + 2, f"words at {t}") for t in range(0, int(secs), 10)]), None


def make_wav(path, seconds):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(i / 10))) for i in range(seconds * 8000)))


class HelperTests(unittest.TestCase):
    def test_srt_time_and_paths(self):
        self.assertEqual(tr._srt_time(3725.5), "01:02:05,500")
        final, partial, prog = tr.output_paths("D:/rec/My Stream.mkv", "C:/out")
        self.assertEqual((final.name, partial.name, prog.name),
                         ("My Stream.srt", "My Stream.srt.partial", "My Stream.progress.json"))
        self.assertEqual(final.parent.name, "Transcripts")


@unittest.skipUnless(HAVE_AV, "PyAV/NumPy not installed")
class TranscribeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = Path(self.tmp.name, "rec.wav")
        make_wav(self.rec, 95)
        self.out = Path(self.tmp.name, "out")
        self.old = tr.CHUNK_SECONDS
        tr.CHUNK_SECONDS = 30

    def tearDown(self):
        tr.CHUNK_SECONDS = self.old
        self.tmp.cleanup()

    def test_chunks_cover_the_whole_file(self):
        pieces = [(at, len(s) / tr.SAMPLE_RATE) for at, s in tr.iter_chunks(str(self.rec))]
        self.assertEqual([a for a, _ in pieces], [0, 30, 60, 90])
        self.assertAlmostEqual(sum(n for _, n in pieces), 95, delta=0.1)
        self.assertEqual([a for a, _ in tr.iter_chunks(str(self.rec), 60)], [60, 90])

    def test_straight_through(self):
        f = tr.transcribe(str(self.rec), str(self.out), model=FakeModel())
        lines = core.parse_caption_text(f.read_text(encoding="utf-8"), ".srt")
        self.assertEqual(lines[0], "[00:00:00] words at 0")
        self.assertEqual(lines[-1], "[00:01:30] words at 0")  # last piece starts at 90 s
        self.assertEqual(len(lines), 3 + 3 + 3 + 1)
        self.assertEqual(sorted(p.name for p in f.parent.iterdir()), ["rec.srt"])  # partial + progress cleaned up

    def test_stop_then_continue(self):
        stop = threading.Event()

        def progress(done, total):
            if done >= 60:
                stop.set()

        with self.assertRaises(tr.Stopped):
            tr.transcribe(str(self.rec), str(self.out), model=FakeModel(), progress=progress, stop=stop)
        self.assertEqual(tr.resume_point(str(self.rec), str(self.out)), 60)
        self.assertEqual([round(u["done_until"]) for u in tr.unfinished(str(self.out))], [60])
        m = FakeModel()
        logs = []
        f = tr.transcribe(str(self.rec), str(self.out), model=m, log=logs.append)
        self.assertEqual(m.calls, 2)  # only the pieces from 60 s on were redone
        self.assertTrue(logs and "00:01:00" in logs[0])
        text = f.read_text(encoding="utf-8")
        lines = core.parse_caption_text(text, ".srt")
        self.assertEqual(len(lines), 10)
        numbers = [ln for ln in text.splitlines() if ln.strip().isdigit()]
        self.assertEqual(numbers, [str(i) for i in range(1, 11)])  # cue numbers continue after resuming

    def test_changed_recording_starts_over(self):
        stop = threading.Event()
        with self.assertRaises(tr.Stopped):
            tr.transcribe(str(self.rec), str(self.out), model=FakeModel(),
                          progress=lambda d, t: d >= 30 and stop.set(), stop=stop)
        make_wav(self.rec, 50)  # a different file with the same name
        os.utime(self.rec, (1, 1))
        self.assertEqual(tr.resume_point(str(self.rec), str(self.out)), 0)

    def test_no_sound_track(self):
        bad = Path(self.tmp.name, "notes.txt")
        bad.write_text("hello", encoding="utf-8")
        with self.assertRaises(Exception):
            tr.transcribe(str(bad), str(self.out), model=FakeModel())


@unittest.skipUnless(HAVE_AV, "PyAV/NumPy not installed")
class LinkTranscribeTests(unittest.TestCase):
    """'Transcribe the links': audio download -> Whisper -> transcript kept -> Gemini -> result."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.tmp.name
        self.downloads, self.sent = [], []
        self.old = (core.generate, tr.load_model, tr.CHUNK_SECONDS)
        core.generate = lambda key, model, system, transcript: self.sent.append(transcript) or "00:00 – Intro\n"
        tr.load_model = lambda name, folder=None: FakeModel()
        tr.CHUNK_SECONDS = 30

    def tearDown(self):
        core.generate, tr.load_model, tr.CHUNK_SECONDS = self.old
        self.tmp.cleanup()

    def runner(self, **kw):
        cfg = core.RunConfig(api_key="k", model="m", prompt="p", output_dir=self.out, delay_min=0, delay_max=0,
                             use_whisper=True, **kw)
        logs = []
        r = core.Runner(cfg, "aaaaaaaaaaa", lambda m, l="info": logs.append(m), lambda *a: None, lambda m: None,
                        threading.Event())
        r._sleep = lambda s: None
        r._fetch_info = lambda url: {"title": "Big: Debate?", "duration": 7200}

        def fake_download(vid, url, info, folder):
            folder.mkdir(parents=True, exist_ok=True)
            p = folder / f"{vid}.wav"
            make_wav(p, 65)
            self.downloads.append(vid)
            return p
        r._download_audio = fake_download
        return r, logs

    def test_link_to_result_in_one_go(self):
        r, logs = self.runner()
        sm = r.run()
        self.assertEqual(sm["done"], 1, logs)
        t = Path(self.out, "Transcripts")
        self.assertTrue((t / "Big Debate [aaaaaaaaaaa].srt").exists())
        self.assertEqual(list((t / "audio").iterdir()), [])  # the audio is deleted once transcribed
        self.assertTrue(Path(self.out, "Big Debate [aaaaaaaaaaa].txt").exists())
        self.assertTrue(self.sent and self.sent[0].startswith("[00:00:00] words at 0"))

    def test_existing_transcript_is_reused(self):
        self.runner()[0].run()
        r, logs = self.runner(skip_existing=False)
        self.assertEqual(r.run()["done"], 1)
        self.assertEqual(self.downloads, ["aaaaaaaaaaa"])  # downloaded and transcribed only once
        self.assertTrue(any("made earlier" in m for m in logs))

    def test_audio_from_an_interrupted_try_is_reused(self):
        folder = Path(self.out, "Transcripts", "audio")
        folder.mkdir(parents=True)
        make_wav(folder / "Big Debate [aaaaaaaaaaa].wav", 40)
        r, logs = self.runner()
        self.assertEqual(r.run()["done"], 1)
        self.assertEqual(self.downloads, [])
        self.assertTrue(any("downloaded earlier" in m for m in logs))

    def test_caption_file_named_with_video_id_gets_a_linkable_result(self):
        srt = Path(self.out, "Old Stream [bbbbbbbbbbb].srt")
        srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n", encoding="utf-8")
        cfg = core.RunConfig(api_key="k", model="m", prompt="p", output_dir=self.out)
        core.Runner(cfg, "", lambda *a: None, lambda *a: None, lambda m: None, threading.Event(),
                    files=[str(srt)]).run()
        self.assertTrue(Path(self.out, "Old Stream [bbbbbbbbbbb].txt").exists())


if __name__ == "__main__":
    unittest.main()
