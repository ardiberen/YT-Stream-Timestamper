"""Tests for the parts of core.py/settings.py that don't need yt-dlp, google-genai or a display.

Run:  python -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
import settings  # noqa: E402
import updates  # noqa: E402
import prompt_builder as pb  # noqa: E402
import usage  # noqa: E402
import worklist  # noqa: E402
import version  # noqa: E402


class UrlTests(unittest.TestCase):
    def test_videos(self):
        vid = "dQw4w9WgXcQ"
        for u in (
            f"https://www.youtube.com/watch?v={vid}",
            f"https://youtu.be/{vid}?t=5",
            f"https://www.youtube.com/live/{vid}",
            f"youtube.com/watch?v={vid}&list=PLabc",
            vid,
        ):
            self.assertEqual(core.classify_url(u), ("video", vid), u)

    def test_playlist_channel(self):
        self.assertEqual(core.classify_url("https://www.youtube.com/playlist?list=PLabc")[0], "playlist")
        self.assertEqual(core.classify_url("https://www.youtube.com/@TheBibleGuy")[0], "channel")
        self.assertEqual(core.classify_url("https://www.youtube.com/channel/UCabc123")[0], "channel")
        self.assertEqual(core.classify_url("https://www.youtube.com/@SomeName/live")[0], "channel")

    def test_other(self):
        self.assertEqual(core.classify_url("")[0], "skip")
        self.assertEqual(core.classify_url("# comment")[0], "skip")
        self.assertEqual(core.classify_url("https://example.com/x")[0], "unknown")
        self.assertEqual(core.classify_url("hello world")[0], "unknown")

    def test_url_values_are_rebuilt_not_passed_through(self):
        c = core.classify_url
        self.assertEqual(c("https://www.youtube.com/playlist?list=PLabc&si=zzz"),
                         ("playlist", "https://www.youtube.com/playlist?list=PLabc"))
        self.assertEqual(c("https://m.youtube.com/@TheBibleGuy/videos?view=0"),
                         ("channel", "https://www.youtube.com/@TheBibleGuy/videos"))
        self.assertEqual(c("https://www.youtube.com/@Foo/about"), ("channel", "https://www.youtube.com/@Foo"))
        self.assertEqual(c("youtube.com/@Foo"), ("channel", "https://www.youtube.com/@Foo"))

    def test_non_youtube_links_are_refused(self):
        bad = (
            "https://evil.example/playlist?list=PLabc",
            "https://evil.example/?x=youtube.com/@Foo",
            "https://evil.example/@Foo",
            "https://youtube.com.evil.example/@Foo",
            "https://www.youtube.com@evil.example/@Foo",
            "https://evil.example\\@www.youtube.com/@Foo",
            "http://localhost/playlist?list=PLabc",
            "file:///C:/Windows/win.ini",
            "ftp://www.youtube.com/@Foo",
            "https://www.youtube.com/@Foo bar",
            "https://www.youtube.com/../../etc/passwd",
        )
        for u in bad:
            self.assertEqual(core.classify_url(u)[0], "unknown", u)

    def test_normalize_channel(self):
        n = core.normalize_channel_url
        self.assertEqual(n("https://www.youtube.com/@Foo", "streams"), "https://www.youtube.com/@Foo/streams")
        self.assertEqual(n("https://www.youtube.com/@Foo/", "videos"), "https://www.youtube.com/@Foo/videos")
        self.assertEqual(n("https://www.youtube.com/@Foo/videos?view=0", "streams"),
                         "https://www.youtube.com/@Foo/videos")


class ParseTests(unittest.TestCase):
    def test_json3(self):
        raw = json.dumps({"events": [
            {"tStartMs": 0, "segs": [{"utf8": "hello "}, {"utf8": "world"}]},
            {"tStartMs": 1000, "segs": [{"utf8": "\n"}]},
            {"tStartMs": 3725000, "segs": [{"utf8": "later"}]},
            {"tStartMs": 4000},
        ]})
        self.assertEqual(core.parse_json3(raw), ["[00:00:00] hello world", "[01:02:05] later"])

    def test_vtt(self):
        raw = ("WEBVTT\nKind: captions\nLanguage: en\n\n"
               "00:00:01.000 --> 00:00:03.000 align:start\nhello <c>there</c>\n\n"
               "00:00:03.000 --> 00:00:05.000\nhello there\n\n"
               "01:00:00.500 --> 01:00:02.000\nnew line\n")
        self.assertEqual(core.parse_vtt(raw), ["[00:00:01] hello there", "[01:00:00] new line"])

    def test_srt_and_sbv(self):
        srt = "1\n00:00:01,000 --> 00:00:02,000\nHi there\n\n2\n01:02:03,500 --> 01:02:04,000\n<i>Later</i>\n"
        self.assertEqual(core.parse_vtt(srt), ["[00:00:01] Hi there", "[01:02:03] Later"])
        self.assertEqual(core.parse_caption_text(srt, ".srt"), ["[00:00:01] Hi there", "[01:02:03] Later"])
        sbv = "0:00:01.000,0:00:02.000\nHi there\n\n1:02:03.500,1:02:04.000\nLater\n"
        self.assertEqual(core.parse_sbv(sbv), ["[00:00:01] Hi there", "[01:02:03] Later"])
        self.assertEqual(core.parse_caption_text(sbv, ""), ["[00:00:01] Hi there", "[01:02:03] Later"])  # sniffed
        self.assertEqual(core.parse_caption_text("\ufeffWEBVTT\n\n00:00:05.000 --> 00:00:06.000\nyo\n", ".vtt"),
                         ["[00:00:05] yo"])
        self.assertEqual(core.parse_caption_text("not captions at all", ".vtt"), ["[00:00:00] not captions at all"])
        self.assertEqual(core.parse_caption_text("{bad json", ".json3"), [])

    def test_pick_caption(self):
        T = "https://www.youtube.com/api/timedtext?v=x&lang=en&fmt=json3"
        # json3 preferred over vtt
        info = {"automatic_captions": {"en-orig": [{"ext": "vtt", "url": T}, {"ext": "json3", "url": T + "&j"}]}}
        self.assertEqual(core.pick_caption(info, "en")[:2], (T + "&j", "json3"))
        # creator-uploaded captions beat auto-generated
        info = {"subtitles": {"en-US": [{"ext": "json3", "url": "m"}]},
                "automatic_captions": {"en-orig": [{"ext": "json3", "url": "a"}]}}
        self.assertEqual(core.pick_caption(info, "en"), ("m", "json3", "en-US", "manual"))
        # unsupported formats only -> None
        self.assertIsNone(core.pick_caption({"automatic_captions": {"en": [{"ext": "ttml", "url": "x"}]}}, "en"))

    def test_never_picks_translated_captions(self):
        # Spanish stream: 'en' auto track is a machine translation (tlang=en) -> must use es-orig instead
        info = {"automatic_captions": {
            "en": [{"ext": "json3", "url": "https://yt/api/timedtext?lang=es&tlang=en&fmt=json3"}],
            "es": [{"ext": "json3", "url": "https://yt/api/timedtext?lang=es&fmt=json3"}],
            "es-orig": [{"ext": "json3", "url": "https://yt/api/timedtext?lang=es&fmt=json3&o"}],
        }}
        url, ext, key, kind = core.pick_caption(info, "en")
        self.assertNotIn("tlang=", url)
        self.assertEqual((key, kind), ("es-orig", "auto"))
        # only translated tracks available -> nothing
        only_tr = {"automatic_captions": {"en": [{"ext": "json3", "url": "https://yt/t?lang=es&tlang=en"}]}}
        self.assertIsNone(core.pick_caption(only_tr, "en"))


class MiscTests(unittest.TestCase):
    def test_filename(self):
        self.assertEqual(core.safe_filename('A: "B"/C?  d.'), "A BC d")
        self.assertEqual(core.safe_filename("???"), "untitled")
        self.assertLessEqual(len(core.safe_filename("x" * 500)), 120)
        for name in ("CON", "nul", "COM1", "lpt9", "aux.txt", "PRN."):
            self.assertNotEqual(core.safe_filename(name).split(".")[0].upper(), name.split(".")[0].upper(), name)
        self.assertEqual(core.safe_filename("Console"), "Console")
        for evil in ("..", "../x", "..\\x", "C:\\Windows\\x", "a/../../b", "x:y"):
            out = core.safe_filename(evil)
            self.assertFalse(any(c in out for c in '/\\:'), evil)
            self.assertNotEqual(out, "..")

    def test_redact(self):
        self.assertEqual(core.redact("bad key AIzaSyABCDEFGH1234 here", "AIzaSyABCDEFGH1234"), "bad key [key hidden] here")
        self.assertEqual(core.redact("no secret", ""), "no secret")
        self.assertEqual(core.redact("abc", "abc"), "abc")  # too short to be a real key: leave alone

    def test_system_prompt(self):
        g = "\n\n" + core.INJECTION_GUARD
        self.assertEqual(core.build_system_prompt("min {min_minutes} {x}", 4.0), "min 4 {x}" + g)
        self.assertEqual(core.build_system_prompt("min {min_minutes}", 2.5), "min 2.5" + g)

    def test_existing(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "Title [abcdefghijk].txt").write_text("x")
            Path(d, "Other [zzzzzzzzzzz].transcript.txt").write_text("x")
            self.assertEqual(core.find_existing_output(d, "abcdefghijk"), "Title [abcdefghijk].txt")
            self.assertIsNone(core.find_existing_output(d, "zzzzzzzzzzz"))

    def test_settings_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "s.json")
            s = settings.load(p)
            self.assertEqual(s["prompts"], {})
            self.assertEqual(s["default_prompt"], "")
            s["prompts"]["Show A"] = "prompt"
            s["unknown_key"] = 1
            settings.save(s, p)
            s2 = settings.load(p)
            self.assertEqual(s2["prompts"], {"Show A": "prompt"})
            self.assertNotIn("unknown_key", s2)
            p.write_text("{not json")
            s3 = settings.load(p)
            self.assertEqual(s3["model"], "gemini-4-hydrogen")
            self.assertTrue(p.with_suffix(".bad").exists())


class FakeRunTests(unittest.TestCase):
    """Full Runner flow with yt-dlp / network / Gemini replaced by fakes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = []
        self.orig_dl, self.orig_gen = core.download_text, core.generate
        core.download_text = lambda url, retries=3, stop=None: json.dumps(
            {"events": [{"tStartMs": 5000, "segs": [{"utf8": "caller one"}]}]})
        self.calls = []

        def fake_gen(key, model, system, transcript):
            self.calls.append((model, system, transcript))
            return "Timestamps:\n\n00:00:05 - A - ~5 minutes\n"

        core.generate = fake_gen

    def tearDown(self):
        core.download_text, core.generate = self.orig_dl, self.orig_gen
        self.tmp.cleanup()

    def make(self, urls, **kw):
        files = kw.pop("files", None)
        cfg = core.RunConfig(api_key="k", model="m", prompt="find {min_minutes} min calls", output_dir=self.tmp.name,
                             delay_min=0, delay_max=0, **kw)
        r = core.Runner(cfg, urls, lambda m, l="info": self.logs.append((l, m)), lambda d, t: None,
                        lambda m: None, threading.Event(), files=files)
        r._sleep = lambda s: r._check_stop()
        infos = {
            "aaaaaaaaaaa": {"title": "Long: stream?", "duration": 7200,
                            "automatic_captions": {"en": [{"ext": "json3", "url": "u"}]}},
            "bbbbbbbbbbb": {"title": "Short", "duration": 600,
                            "automatic_captions": {"en": [{"ext": "json3", "url": "u"}]}},
            "ccccccccccc": {"title": "Live now", "duration": 9999, "live_status": "is_live"},
            "ddddddddddd": {"title": "No caps", "duration": 7200},
        }
        r._fetch_info = lambda url: infos[url.rsplit("=", 1)[1]]
        r._list_entries = lambda url: [{"id": "aaaaaaaaaaa"}, {"id": "bbbbbbbbbbb"}]
        return r

    def test_flow(self):
        urls = ("https://youtu.be/aaaaaaaaaaa\nbbbbbbbbbbb\nccccccccccc\nddddddddddd\n"
                "https://www.youtube.com/@Chan\naaaaaaaaaaa\n")
        r = self.make(urls)
        sm = r.run()
        self.assertEqual((sm["done"], sm["skipped"], sm["failed"], sm["waiting"]), (1, 1, 0, 2), self.logs)
        # still live / no captions yet -> waiting list, with titles, and an outcome for every video
        self.assertEqual(sm["waiting_list"], [("ccccccccccc", "Live now"), ("ddddddddddd", "No caps")])
        self.assertEqual(sm["outcomes"], {"aaaaaaaaaaa": "done", "bbbbbbbbbbb": "skipped",
                                          "ccccccccccc": "waiting", "ddddddddddd": "waiting"})
        out = Path(self.tmp.name, "Long stream [aaaaaaaaaaa].txt")
        self.assertTrue(out.exists())
        self.assertIn("00:00:05", out.read_text())
        model, system, transcript = self.calls[0]
        self.assertEqual(system, "find 4 min calls\n\n" + core.INJECTION_GUARD)
        self.assertEqual(transcript, "[00:00:05] caller one")
        # second run: everything done is skipped without a network fetch
        r2 = self.make("aaaaaaaaaaa")
        r2._fetch_info = lambda url: (_ for _ in ()).throw(AssertionError("should not fetch"))
        self.assertEqual(r2.run()["skipped"], 1)

    def test_validation(self):
        r = self.make("aaaaaaaaaaa")
        r.cfg.prompt = "tell me <<WHAT>>"
        self.assertIn("placeholders", r.run()["error"])
        r = self.make("aaaaaaaaaaa")
        r.cfg.prompt = "  "
        self.assertIn("prompt is empty", r.run()["error"])
        r = self.make("aaaaaaaaaaa")
        r.cfg.api_key = ""
        self.assertIn("API key", r.run()["error"])

    def test_bot_check_stops_whole_run(self):
        r = self.make("aaaaaaaaaaa\nbbbbbbbbbbb")
        calls = []

        def boom(url):
            calls.append(url)
            raise RuntimeError("ERROR: [youtube] x: Sign in to confirm you're not a bot. Use --cookies-from-browser")

        r._fetch_info = boom
        sm = r.run()
        self.assertTrue(sm["blocked"])
        self.assertEqual(len(calls), 1)  # stopped after the first block, no hammering
        self.assertEqual(sm["done"], 0)

    def test_caption_429_stops_whole_run(self):
        def limited(url, retries=3, stop=None):
            raise core.YouTubeBlocked("429")

        core.download_text = limited
        sm = self.make("aaaaaaaaaaa\nbbbbbbbbbbb").run()
        self.assertTrue(sm["blocked"])

    def test_age_restricted_is_per_video(self):
        r = self.make("aaaaaaaaaaa\nbbbbbbbbbbb")
        real = r._fetch_info

        def fetch(url):
            if url.endswith("aaaaaaaaaaa"):
                raise RuntimeError("ERROR: [youtube] aaaaaaaaaaa: Sign in to confirm your age. This video may be "
                                   "inappropriate for some users.")
            return real(url)

        r._fetch_info = fetch
        sm = r.run()
        self.assertFalse(sm["blocked"])
        self.assertEqual(sm["needs_file"], ["aaaaaaaaaaa"])
        self.assertEqual(sm["skipped"], 1)  # bbbbbbbbbbb (too short) still got checked

    def test_per_run_cap(self):
        sm = self.make("aaaaaaaaaaa\nbbbbbbbbbbb\nccccccccccc", max_per_run=2).run()
        self.assertEqual(sm["capped"], 1)
        self.assertEqual(sm["done"] + sm["skipped"] + sm["failed"], 2)

    def test_caption_files(self):
        d = Path(self.tmp.name)
        vtt = d / "My Stream.vtt"
        vtt.write_text("WEBVTT\n\n00:10:00.000 --> 00:10:02.000\ncaller joins\n", encoding="utf-8")
        bad = d / "empty.srt"
        bad.write_text("", encoding="utf-8")
        fetched = []
        r = self.make("", files=[str(vtt), str(bad)])
        r._fetch_info = lambda url: fetched.append(url)
        sm = r.run()
        self.assertEqual((sm["done"], sm["failed"]), (1, 1), self.logs)
        self.assertEqual(fetched, [])  # never touched YouTube
        self.assertTrue((d / "My Stream.txt").exists())
        self.assertEqual(self.calls[-1][2], "[00:10:00] caller joins")
        sm2 = self.make("", files=[str(vtt)]).run()  # running again never overwrites
        self.assertTrue(sm2["saved"][0].endswith("My Stream (2).txt"))

    def test_api_key_problem(self):
        def bad(*a):
            raise RuntimeError("400 API key not valid. Please pass a valid API key.")

        core.generate = bad
        sm = self.make("aaaaaaaaaaa").run()
        self.assertTrue(sm["api_key_problem"])

    def test_retry_then_ok(self):
        n = {"i": 0}

        def flaky(key, model, system, transcript):
            n["i"] += 1
            if n["i"] < 3:
                raise RuntimeError("503 UNAVAILABLE")
            return "ok text"

        core.generate = flaky
        r = self.make("aaaaaaaaaaa")
        r._sleep = lambda s: None
        sm = r.run()
        self.assertEqual(sm["done"], 1)
        self.assertEqual(n["i"], 3)

    def test_stop(self):
        r = self.make("aaaaaaaaaaa\nbbbbbbbbbbb")
        r.stop.set()
        sm = r.run()
        self.assertTrue(sm["stopped"])
        self.assertEqual(sm["done"], 0)



class PromptBuilderTests(unittest.TestCase):
    def setUp(self):
        import prompt_builder as pb
        self.pb = pb

    def test_all_builtins_build(self):
        for name in self.pb.BUILTIN_PRESETS:
            form = self.pb.builtin_form(name)
            if name.startswith("Blank"):
                self.assertTrue(self.pb.validate_form(form))  # 'find' is required
                form["find"] = "something happens"
            self.assertEqual(self.pb.validate_form(form), [], name)
            text = self.pb.build_prompt(form)
            self.assertNotIn("<<", text)
            self.assertIn("HH:MM:SS", text)

    def test_original_case(self):
        form = self.pb.builtin_form("Debate call-in show (callers who disagree with the host)")
        form["show"] = "The Bible Guy"
        t = self.pb.build_prompt(form)
        self.assertIn('"The Bible Guy"', t)
        self.assertIn("HH:MM:SS – Name – Stance – Length: ~X minutes", t)
        self.assertIn("single period", t)
        self.assertIn("{min_minutes}", t)
        # and the runner turns the placeholder into the number
        self.assertIn("LESS THAN 4 minutes", core.build_system_prompt(t, 4.0))
        self.assertNotIn("{min_minutes}", core.build_system_prompt(t, 4.0))

    def test_options(self):
        f = self.pb.normalize_form({"find": "x", "min_minutes": "0", "name_on": False, "length_on": False,
                                    "summary_on": True, "separator": "|", "header": "", "blank_between": False})
        t = self.pb.build_prompt(f)
        self.assertNotIn("{min_minutes}", t)  # no minimum -> no exclusion lines
        self.assertIn("HH:MM:SS | One-line summary", t)
        self.assertNotIn("Output format required:\nTimestamps:", t)
        self.assertIn("no blank lines", t)
        self.assertNotIn("single period", t)

    def test_chapters(self):
        for name in self.pb.BUILTIN_PRESETS:  # every built-in preset starts with chapters on
            f = self.pb.builtin_form(name)
            self.assertTrue(f["chapters"], name)
            self.assertIn('"00:00 – Intro" exactly', self.pb.build_prompt(f), name)
        f["chapters"] = False
        self.assertNotIn("00:00 –", self.pb.build_prompt(f))
        f["chapters"] = True
        f["chapter_intro"] = "Start"
        t = self.pb.build_prompt(f)
        self.assertIn("00:00 – Start", t)
        self.assertIn("at least 10 seconds", t)

    def test_validation(self):
        self.assertTrue(self.pb.validate_form({"find": ""}))
        self.assertTrue(self.pb.validate_form({"find": "x", "min_minutes": "abc"}))
        self.assertTrue(self.pb.validate_form({"find": "x", "min_minutes": "-1"}))
        self.assertEqual(self.pb.validate_form({"find": "x", "min_minutes": "2.5"}), [])


class SavedListTests(FakeRunTests):
    def test_saved_list(self):
        sm = self.make("aaaaaaaaaaa").run()
        self.assertEqual(len(sm["saved"]), 1)


class AccountSafetyTests(unittest.TestCase):
    def test_safe_args(self):
        for flag in ("--ignore-config", "--no-cookies", "--no-cookies-from-browser"):
            self.assertIn(flag, core.SAFE_YTDLP_ARGS)

    def test_ytdlp_options_have_no_login(self):
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            self.skipTest("yt-dlp not installed")
        with tempfile.TemporaryDirectory() as d:  # a user config that tries to add a login is ignored
            os.makedirs(os.path.join(d, "yt-dlp"))
            Path(d, "yt-dlp", "config").write_text("--cookies-from-browser firefox\n--cookies x.txt\n")
            Path(d, "yt-dlp", "config.txt").write_text("--cookies-from-browser chrome\n")
            old = {k: os.environ.get(k) for k in ("XDG_CONFIG_HOME", "APPDATA")}
            os.environ["XDG_CONFIG_HOME"] = os.environ["APPDATA"] = d
            try:
                opts = core.build_ydl_opts([], None, core._YtLogger(lambda *a: None))
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
        for k in ("cookiefile", "cookiesfrombrowser", "username", "password", "usenetrc"):
            self.assertFalse(opts.get(k), k)
        self.assertEqual(opts["extractor_args"]["youtube"]["skip"], ["hls", "dash", "translated_subs"])


class SecurityTests(FakeRunTests):
    """Hardening: what the app will and won't fetch, and where secrets can leak."""

    def test_foreign_links_never_reach_ytdlp(self):
        r = self.make("https://evil.example/playlist?list=PLabc\nhttps://evil.example/@Foo\nfile:///C:/x\n")
        seen = []
        r._list_entries = lambda url: seen.append(url) or []
        r._fetch_info = lambda url: seen.append(url) or {}
        sm = r.run()
        self.assertEqual(seen, [])
        self.assertEqual(sm["done"] + sm["failed"] + sm["skipped"], 0)
        self.assertTrue(any("Not a YouTube link" in m for _, m in self.logs))

    def test_only_rebuilt_youtube_urls_are_passed_on(self):
        r = self.make("https://m.youtube.com/@Foo?utm=1#x\nhttps://youtube.com/playlist?list=PLabc&junk=1")
        seen = []
        r._list_entries = lambda url: seen.append(url) or []
        r.run()
        self.assertEqual(seen, ["https://www.youtube.com/@Foo/streams",
                                "https://www.youtube.com/playlist?list=PLabc"])

    def test_download_text_refuses_unsafe_addresses(self):
        core.download_text = self.orig_dl  # the real function; none of these may touch the network
        for u in ("file:///C:/Windows/win.ini", "http://www.youtube.com/api/timedtext?x=1",
                  "https://evil.example/api/timedtext", "https://www.youtube.com.evil.example/x",
                  "https://www.youtube.com@evil.example/x", "ftp://www.youtube.com/x", "", "not a url"):
            with self.assertRaises(core._Refused, msg=u):
                core.download_text(u)
        for u in ("https://www.youtube.com/api/timedtext?v=x", "https://rr1---sn-x.googlevideo.com/a"):
            self.assertTrue(core._caption_url_ok(u), u)

    def test_redirects_off_youtube_are_refused(self):
        h = core._SafeRedirects()
        import urllib.request as ur
        req = ur.Request("https://www.youtube.com/api/timedtext")
        with self.assertRaises(core._Refused):
            h.redirect_request(req, None, 302, "Found", {}, "https://evil.example/steal")
        with self.assertRaises(core._Refused):
            h.redirect_request(req, None, 302, "Found", {}, "file:///etc/passwd")
        self.assertIsNotNone(h.redirect_request(req, None, 302, "Found", {}, "https://www.youtube.com/other"))

    def test_oversized_caption_file_refused(self):
        big = Path(self.tmp.name, "big.vtt")
        big.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello\n")
        old = core.MAX_DOWNLOAD_BYTES
        core.MAX_DOWNLOAD_BYTES = 10
        try:
            r = self.make("", files=[str(big)])
            sm = r.run()
        finally:
            core.MAX_DOWNLOAD_BYTES = old
        self.assertEqual((sm["done"], sm["failed"]), (0, 1))
        self.assertTrue(any("too large" in m for _, m in self.logs))

    def test_api_key_never_logged(self):
        key = "AIzaSyTESTKEY1234567890"

        def boom(k, model, system, transcript):
            raise RuntimeError(f"400 INVALID_ARGUMENT for key {k} at https://x/?key={k}")

        core.generate = boom
        r = self.make("aaaaaaaaaaa")
        r.cfg.api_key = key
        r.cfg.max_retries = 1
        sm = r.run()
        blob = "\n".join(m for _, m in self.logs) + str(sm)
        self.assertNotIn(key, blob)
        self.assertIn("Gemini error", blob)
        ok, msg = core.test_api_key(key, "m")
        self.assertNotIn(key, msg)

    def test_ytdlp_restricted_to_youtube_and_no_remote_code(self):
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            self.skipTest("yt-dlp not installed")
        opts = core.build_ydl_opts([], "/x/deno", core._YtLogger(lambda *a: None))
        self.assertEqual(opts["allowed_extractors"], ["youtube.*"])
        self.assertFalse(opts.get("remote_components"))
        ydl = yt_dlp.YoutubeDL({"allowed_extractors": opts["allowed_extractors"], "quiet": True})
        self.assertFalse(any("generic" in ie.IE_NAME.lower() for ie in ydl._ies.values()))
        self.assertEqual([ie.IE_NAME for ie in ydl._ies.values() if ie.suitable("https://evil.example/playlist?list=PL1")], [])


class WritableFolderTests(unittest.TestCase):
    def test_ok_and_creates_folder(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "new", "sub")
            self.assertIsNone(core.check_writable(target))
            self.assertTrue(os.path.isdir(target))
            self.assertEqual(os.listdir(target), [])  # probe file removed

    def test_blocked_folder_gets_plain_message(self):
        real = os.makedirs
        os.makedirs = lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "Access is denied"))
        try:
            msg = core.check_writable("D:\\Docs\\Stream Timestamps")
        finally:
            os.makedirs = real
        self.assertIn("Controlled folder access", msg)
        self.assertIn("Stream Timestamps", msg)

    def test_other_errors(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "afile")
            f.write_text("x")
            self.assertIn("Can't save", core.check_writable(str(Path(f, "inside"))))

    def test_run_stops_before_calling_gemini(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            orig = core.generate
            core.generate = lambda *a: calls.append(a) or "x"
            f = Path(d, "afile")
            f.write_text("x")
            try:
                cfg = core.RunConfig(api_key="k", model="m", prompt="p", output_dir=str(Path(f, "out")))
                sm = core.Runner(cfg, "aaaaaaaaaaa", lambda m, l="info": None, lambda *a: None, lambda m: None,
                                 threading.Event()).run()
            finally:
                core.generate = orig
            self.assertIn("Can't save", sm["error"])
            self.assertEqual(calls, [])


class SecretStorageTests(unittest.TestCase):
    """API key encryption at rest, with DPAPI replaced by a reversible fake (real DPAPI only exists on Windows)."""

    KEY = "AIzaSyTESTKEY1234567890"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name, "s.json")
        self.orig = settings._dpapi
        self.calls = []

    def tearDown(self):
        settings._dpapi = self.orig
        self.tmp.cleanup()

    def install_fake(self):
        def f(data, protect):
            if not data:
                return None
            return b"ENC" + bytes(b ^ 0x5A for b in data) if protect else bytes(b ^ 0x5A for b in data[3:])
        settings._dpapi = f

    def test_encrypted_on_disk_and_roundtrip(self):
        self.install_fake()
        s = settings.load(self.path)
        s["api_key"] = self.KEY
        settings.save(s, self.path)
        raw = self.path.read_text()
        self.assertNotIn(self.KEY, raw)
        self.assertIn('"api_key_enc": "dpapi:', raw)
        s2 = settings.load(self.path)
        self.assertEqual(s2["api_key"], self.KEY)
        self.assertNotIn("api_key_enc", s2)  # never kept in memory

    def test_falls_back_to_plain_text_when_unavailable(self):
        settings._dpapi = lambda data, protect: None  # e.g. not Windows
        s = settings.load(self.path)
        s["api_key"] = self.KEY
        settings.save(s, self.path)
        self.assertEqual(json.loads(self.path.read_text())["api_key"], self.KEY)
        self.assertEqual(settings.load(self.path)["api_key"], self.KEY)

    def test_never_stores_a_key_it_cannot_read_back(self):
        settings._dpapi = lambda data, protect: b"garbage" if protect else b"different"
        s = settings.load(self.path)
        s["api_key"] = self.KEY
        settings.save(s, self.path)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["api_key"], self.KEY)
        self.assertEqual(saved["api_key_enc"], "")

    def test_old_plain_text_file_is_upgraded_on_next_save(self):
        self.install_fake()
        self.path.write_text(json.dumps({"api_key": self.KEY, "model": "m"}))
        s = settings.load(self.path)
        self.assertEqual(s["api_key"], self.KEY)
        settings.save(s, self.path)
        self.assertNotIn(self.KEY, self.path.read_text())

    def test_unreadable_token_means_ask_for_key_again(self):
        self.install_fake()
        for bad in ("dpapi:!!!notbase64", "dpapi:", "plain-text-not-a-token"):
            self.path.write_text(json.dumps({"api_key": "", "api_key_enc": bad}))
            self.assertEqual(settings.load(self.path)["api_key"], "", bad)
        settings._dpapi = lambda data, protect: None  # file copied to another PC/user: decrypt fails
        self.path.write_text(json.dumps({"api_key": "", "api_key_enc": "dpapi:QUJDREVG"}))
        self.assertEqual(settings.load(self.path)["api_key"], "")

    def test_empty_key(self):
        self.install_fake()
        s = settings.load(self.path)
        settings.save(s, self.path)
        self.assertEqual(settings.load(self.path)["api_key"], "")


class UpdateCheckTests(unittest.TestCase):
    """The 'new version available' check: strict parsing, no downloads, never raises."""

    def setUp(self):
        self.orig = updates.fetch_latest_tag

    def tearDown(self):
        updates.fetch_latest_tag = self.orig

    def test_parse_version(self):
        p = updates.parse_version
        self.assertEqual(p("v0.5.0"), (0, 5, 0))
        self.assertEqual(p("1.12.3"), (1, 12, 3))
        for bad in ("", None, "v1.2", "1.2.3-beta", "v1.2.3.4", "<script>", "v1.2.3 ", "latest", "v1.2.x"):
            if bad == "v1.2.3 ":
                self.assertEqual(p(bad), (1, 2, 3))  # surrounding spaces are fine
            else:
                self.assertIsNone(p(bad), bad)
        self.assertGreater(p("0.10.0"), p("0.9.9"))  # numeric, not text, comparison

    def test_repo_names(self):
        self.assertTrue(updates.repo_ok("ardiberen/stream-timestamper"))
        for bad in ("", "noslash", "a/b/c", "../etc/passwd", "evil.com/x", "a b/c", "owner/", "/repo",
                    "owner/repo?x=1", "owner/repo#x"):
            self.assertFalse(updates.repo_ok(bad), bad)

    def test_links_are_built_locally(self):
        self.assertEqual(updates.releases_page("me/app"), "https://github.com/me/app/releases/latest")
        self.assertEqual(updates.help_page("me/app"), "https://github.com/me/app#getting-help")

    def test_check_results(self):
        cases = [("v0.6.0", ("newer", "0.6.0")), ("v0.5.0", ("current", None)), ("0.4.9", ("current", None)),
                 ("v0.6.0-rc1", ("failed", None)), (None, ("failed", None)), ("<b>v9.9.9</b>", ("failed", None))]
        for tag, want in cases:
            updates.fetch_latest_tag = lambda repo, timeout=8, t=tag: t
            self.assertEqual(updates.check("me/app", "0.5.0"), want, tag)

    def test_check_never_raises(self):
        def boom(repo, timeout=8):
            raise OSError("offline")
        updates.fetch_latest_tag = boom
        self.assertEqual(updates.check("me/app", "0.5.0"), ("failed", None))
        updates.fetch_latest_tag = self.orig
        self.assertEqual(updates.check("not a repo", "0.5.0"), ("failed", None))  # rejected before any network

    def test_redirects_must_stay_on_api_github(self):
        import urllib.request as ur
        h = updates._GitHubOnlyRedirects()
        req = ur.Request("https://api.github.com/repos/me/app/releases/latest")
        for bad in ("https://evil.example/x", "http://api.github.com/x", "https://api.github.com.evil.example/x",
                    "file:///etc/passwd"):
            with self.assertRaises(updates._Refused, msg=bad):
                h.redirect_request(req, None, 301, "Moved", {}, bad)
        self.assertIsNotNone(h.redirect_request(req, None, 301, "Moved", {},
                                                "https://api.github.com/repositories/1/releases/latest"))

    def test_daily_throttle(self):
        now = 1_000_000.0
        self.assertTrue(updates.is_due(0, now))
        self.assertFalse(updates.is_due(now - 3600, now))
        self.assertTrue(updates.is_due(now - 25 * 3600, now))
        self.assertTrue(updates.is_due(now + 10_000, now))  # clock moved backwards
        self.assertTrue(updates.is_due("garbage", now))

    def test_local_build_has_no_repo(self):
        if not Path(version.__file__).with_name("build_info.py").exists():
            self.assertEqual(version.GITHUB_REPO, "")
        self.assertIsNotNone(updates.parse_version(version.APP_VERSION))


class TlsContextTests(unittest.TestCase):
    """Our own HTTPS requests must trust the Windows store AND certifi, with verification fully on."""

    def test_verification_stays_on(self):
        import ssl
        import net_ssl
        ctx = net_ssl.make_context()
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_certifi_bundle_is_loaded(self):
        import net_ssl
        import ssl
        try:
            import certifi
        except ImportError:
            self.skipTest("certifi not installed here")
        base = ssl.create_default_context().cert_store_stats()["x509_ca"]
        self.assertGreaterEqual(net_ssl.make_context().cert_store_stats()["x509_ca"], base)
        self.assertGreater(net_ssl.make_context().cert_store_stats()["x509_ca"], 0)

    def test_works_without_certifi(self):
        import builtins
        import net_ssl
        real = builtins.__import__

        def no_certifi(name, *a, **k):
            if name == "certifi":
                raise ImportError("simulated")
            return real(name, *a, **k)
        builtins.__import__ = no_certifi
        try:
            ctx = net_ssl.make_context()
        finally:
            builtins.__import__ = real
        import ssl
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_both_downloaders_use_the_handler(self):
        import inspect
        self.assertIn("net_ssl.https_handler()", inspect.getsource(core.download_text))
        self.assertIn("net_ssl.https_handler()", inspect.getsource(updates.fetch_latest_tag))


class NetworkTrustTests(FakeRunTests):
    """Certificate failures (e.g. a network that inspects HTTPS) stop the run with a plain message."""

    TLS = "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate"

    def test_detection(self):
        import ssl
        self.assertTrue(core.is_tls_problem(RuntimeError(self.TLS)))
        self.assertTrue(core.is_tls_problem(ssl.SSLCertVerificationError("x")))
        for other in ("timed out", "HTTP 503", "SSL: WRONG_VERSION_NUMBER", "Connection reset"):
            self.assertFalse(core.is_tls_problem(RuntimeError(other)), other)

    def test_gemini_tls_stops_run_with_plain_message(self):
        core.generate = lambda *a: (_ for _ in ()).throw(RuntimeError(self.TLS))
        sm = self.make("aaaaaaaaaaa\nhttps://youtu.be/ddddddddddd").run()
        self.assertIn("network you're on", sm["error"])
        self.assertIn("Gemini", sm["error"])
        self.assertFalse(any("empty answer" in m for _, m in self.logs), self.logs)
        self.assertEqual(sm["done"], 0)

    def test_caption_tls_not_retried(self):
        calls = []

        def boom(url, retries=3, stop=None):
            calls.append(url)
            raise core.NetworkTrustProblem(core.TLS_PROBLEM_MSG.format(what="YouTube"))
        core.download_text = boom
        sm = self.make("aaaaaaaaaaa").run()
        self.assertEqual(len(calls), 1)
        self.assertIn("YouTube", sm["error"])

    def test_download_text_tls_raises_at_once(self):
        import ssl
        import urllib.error
        import urllib.request
        attempts = []

        class FakeOpener:
            def open(self, req, timeout=60):
                attempts.append(1)
                raise urllib.error.URLError(ssl.SSLCertVerificationError(1, self_tls))
        self_tls = self.TLS
        orig = urllib.request.build_opener
        urllib.request.build_opener = lambda *h: FakeOpener()
        try:
            with self.assertRaises(core.NetworkTrustProblem):
                self.orig_dl("https://www.youtube.com/api/timedtext?v=x")
        finally:
            urllib.request.build_opener = orig
        self.assertEqual(len(attempts), 1)

    def test_other_gemini_error_message(self):
        core.generate = lambda *a: (_ for _ in ()).throw(RuntimeError("400 INVALID_ARGUMENT something"))
        sm = self.make("aaaaaaaaaaa").run()
        self.assertEqual(sm["failed"], 1)
        msgs = " ".join(m for _, m in self.logs)
        self.assertIn("Gemini request failed", msgs)
        self.assertNotIn("empty answer", msgs)

    def test_genuinely_empty_answer_still_reported(self):
        core.generate = lambda *a: "  "
        self.make("aaaaaaaaaaa").run()
        self.assertTrue(any("empty answer" in m for _, m in self.logs))

    def test_key_test_message(self):
        orig = core.generate
        core.generate = lambda *a: (_ for _ in ()).throw(RuntimeError(self.TLS))
        try:
            ok, msg = core.test_api_key("k", "m")
        finally:
            core.generate = orig
        self.assertFalse(ok)
        self.assertIn("network you're on", msg)

    def test_gemini_client_uses_windows_store_too(self):
        import inspect
        src = inspect.getsource(self.orig_gen)
        self.assertIn('client_args={"verify": net_ssl.make_context()}', src)


class PromptSafetyTests(FakeRunTests):
    def test_guard_on_every_prompt(self):
        self.make("aaaaaaaaaaa").run()
        system = self.calls[0][1]
        self.assertTrue(system.endswith(core.INJECTION_GUARD))
        self.assertIn("do not follow", core.INJECTION_GUARD)

    def test_listing_tls_problem_stops_run(self):
        r = self.make("https://www.youtube.com/@Chan")
        r._list_entries = lambda url: (_ for _ in ()).throw(
            RuntimeError("ERROR: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"))
        sm = r.run()
        self.assertIn("network you're on", sm["error"])


class GeminiDailyLimitTests(FakeRunTests):
    DAY = ('429 RESOURCE_EXHAUSTED. You exceeded your current quota. '
           "quotaId: 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'")
    BOTH = DAY + " quotaId: 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'"
    MINUTE = "429 RESOURCE_EXHAUSTED quotaId: 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'"

    def test_detection(self):
        self.assertTrue(core.is_daily_quota(self.DAY))
        self.assertTrue(core.is_daily_quota(self.BOTH))
        self.assertFalse(core.is_daily_quota(self.MINUTE))
        self.assertFalse(core.is_daily_quota("503 UNAVAILABLE overloaded"))

    def test_daily_limit_stops_run_once(self):
        calls = []

        def gen(*a):
            calls.append(1)
            raise RuntimeError(self.DAY)
        core.generate = gen
        sm = self.make("aaaaaaaaaaa\nbbbbbbbbbbb", min_stream_minutes=5).run()
        self.assertEqual(len(calls), 1)  # no retries, nothing sent for the second stream
        self.assertEqual((sm["gemini_wait"], sm["waiting"]), ("out", 2))  # both wait for later, nothing failed
        self.assertEqual([v for v, _ in sm["waiting_list"]], ["aaaaaaaaaaa", "bbbbbbbbbbb"])
        self.assertTrue(any("midnight Pacific" in m for _, m in self.logs))

    def test_minute_and_day_listed_waits_once_then_continues(self):
        calls = []

        def gen(*a):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError(self.BOTH)
            return "Timestamps:\n00:00:05 - A\n"
        core.generate = gen
        sm = self.make("aaaaaaaaaaa").run()
        self.assertEqual((len(calls), sm["done"]), (2, 1))
        self.assertTrue(any("per-minute or the daily" in m for _, m in self.logs))

    def test_minute_and_day_twice_means_daily(self):
        calls = []

        def gen(*a):
            calls.append(1)
            raise RuntimeError(self.BOTH)
        core.generate = gen
        sm = self.make("aaaaaaaaaaa").run()
        self.assertEqual(len(calls), 2)
        self.assertEqual(sm["gemini_wait"], "out")

    def test_per_minute_still_retried(self):
        calls = []

        def gen(*a):
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError(self.MINUTE)
            return "ok text"
        core.generate = gen
        sm = self.make("aaaaaaaaaaa").run()
        self.assertEqual((len(calls), sm["done"]), (3, 1))

    def test_daily_limit_switches_to_backup_for_rest_of_run(self):
        models = []

        def gen(key, model, system, transcript):
            models.append(model)
            if model == "m":
                raise RuntimeError(self.DAY)
            return "Timestamps:\n00:00:05 - A\n"
        core.generate = gen
        sm = self.make("aaaaaaaaaaa\nbbbbbbbbbbb", backup_model="lite", min_stream_minutes=5).run()
        self.assertEqual(sm["done"], 2)
        self.assertEqual(models, ["m", "lite", "lite"])  # main model isn't asked again once it's out for the day

    def test_backup_daily_limit_also_stops(self):
        calls = []

        def gen(*a):
            calls.append(a[1])
            raise RuntimeError(self.DAY)
        core.generate = gen
        sm = self.make("aaaaaaaaaaa", backup_model="lite").run()
        self.assertEqual(calls, ["m", "lite"])
        self.assertEqual((sm["gemini_wait"], sm["gemini_wait_model"]), ("out", "lite"))

    def test_key_test_message(self):
        orig = core.generate
        core.generate = lambda *a: (_ for _ in ()).throw(RuntimeError(self.DAY))
        try:
            ok, msg = core.test_api_key("k", "gemini-3.8-flash")
        finally:
            core.generate = orig
        self.assertFalse(ok)
        self.assertIn("daily limit", msg)


class RunLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_and_keeps_newest(self):
        import runlog
        for i in range(12):
            Path(self.dir, f"run-2026-01-{i + 1:02d}_00-00-00.txt").write_text("old")
        Path(self.dir, "notes.txt").write_text("keep me")
        log = runlog.RunLog(self.dir, keep=10)
        log.start(["header"])
        log.write("[12:00:00] hello")
        log.close()
        runs = sorted(p.name for p in self.dir.glob("run-*.txt"))
        self.assertEqual(len(runs), 10)
        self.assertIn(log.path.name, runs)
        self.assertNotIn("run-2026-01-01_00-00-00.txt", runs)
        self.assertTrue(Path(self.dir, "notes.txt").exists())  # only its own files are pruned
        self.assertEqual(log.path.read_text(encoding="utf-8"), "header\n[12:00:00] hello\n")

    def test_failures_never_raise(self):
        import runlog
        blocker = Path(self.dir, "afile")
        blocker.write_text("x")
        log = runlog.RunLog(blocker / "logs")  # can't create a folder inside a file
        log.start(["h"])
        log.write("line")
        log.close()
        self.assertIsNone(log.path)

    def test_write_after_close_is_ignored(self):
        import runlog
        log = runlog.RunLog(self.dir)
        log.start()
        log.close()
        log.write("late")
        self.assertEqual(log.path.read_text(encoding="utf-8"), "")


class ResultsListTests(unittest.TestCase):
    def test_list_and_title(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d, "Show A [aaaaaaaaaaa].txt"); a.write_text("x")
            Path(d, "Show A [aaaaaaaaaaa].transcript.txt").write_text("x")
            b = Path(d, "Show B [bbbbbbbbbbb].txt"); b.write_text("x")
            os.utime(a, (1, 1))
            items = core.list_results(d)
            self.assertEqual([os.path.basename(p) for _, p in items], [b.name, a.name])
            self.assertEqual(core.result_title(str(a)), "Show A")
            self.assertEqual(core.list_results(os.path.join(d, "missing")), [])


class VersionInfoTests(unittest.TestCase):
    """The exe's Windows 'Details' data is generated from version.py; make sure it stays valid."""

    def _load(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "assets"))
        try:
            import make_version_info as mvi
        finally:
            sys.path.pop(0)
        return mvi

    def test_text_parses_and_matches_version(self):
        mvi = self._load()
        seen = {}

        class Rec:
            def __init__(self, *a, **kw):
                self.a, self.kw = a, kw

        def struct(k, v):
            seen[k] = v
            return Rec()

        env = {n: Rec for n in ("VSVersionInfo", "FixedFileInfo", "StringFileInfo", "StringTable",
                                "VarFileInfo", "VarStruct")}
        env["StringStruct"] = struct
        ffi = eval(mvi.build_text("1.2.3"), {"__builtins__": {}}, env).kw["ffi"]
        self.assertEqual(ffi.kw["filevers"], (1, 2, 3, 0))
        self.assertEqual(seen["FileVersion"], "1.2.3.0")
        self.assertEqual(seen["ProductName"], "Stream Timestamper")
        self.assertEqual(seen["OriginalFilename"], "StreamTimestamper.exe")

    def test_version_tuple(self):
        mvi = self._load()
        self.assertEqual(mvi.version_tuple("0.7.0"), (0, 7, 0, 0))
        self.assertEqual(mvi.version_tuple("0.7"), (0, 7, 0, 0))
        self.assertEqual(mvi.build_text().count(version.APP_VERSION), 2)


if __name__ == "__main__":
    unittest.main()


class GeminiRetryTests(FakeRunTests):
    def test_busy_for_every_try_then_backup_and_cooloff(self):
        models, waits = [], []

        def gen(key, model, system, transcript):
            models.append(model)
            if model == "m":
                raise RuntimeError("503 UNAVAILABLE. The model is overloaded.")
            return "ok text"
        core.generate = gen
        cool = {}
        r = self.make("aaaaaaaaaaa\nbbbbbbbbbbb", backup_model="lite", min_stream_minutes=5, cooloff=cool)
        r._sleep = waits.append
        sm = r.run()
        self.assertEqual(sm["done"], 2)
        # 3 tries on the busy model, then the backup; the busy model isn't asked again for the next stream
        self.assertEqual(models, ["m", "m", "m", "lite", "lite"])
        self.assertGreater(cool["m"], __import__("time").time() + 14 * 60)

    def test_no_backup_gives_up_after_max_retries(self):
        calls = []

        def gen(*a):
            calls.append(1)
            raise RuntimeError("503 UNAVAILABLE")
        core.generate = gen
        r = self.make("aaaaaaaaaaa", max_retries=3)
        r._sleep = lambda s: None
        sm = r.run()
        self.assertEqual((len(calls), sm["failed"], sm["waiting"], sm["gemini_wait"]), (3, 0, 1, "busy"))

    def test_uses_googles_retry_delay(self):
        waits = []
        n = {"i": 0}

        def gen(*a):
            n["i"] += 1
            if n["i"] == 1:
                raise RuntimeError("429 RESOURCE_EXHAUSTED quotaId: 'GenerateRequestsPerMinutePerProjectPerModel'"
                                   " {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '37s'}")
            return "ok text"
        core.generate = gen
        r = self.make("aaaaaaaaaaa")
        r._sleep = waits.append
        r.run()
        self.assertIn(37.0, waits)

    def test_backoff_grows(self):
        waits = []
        core.generate = lambda *a: (_ for _ in ()).throw(RuntimeError("503 UNAVAILABLE"))
        r = self.make("aaaaaaaaaaa", max_retries=3)
        r._sleep = waits.append
        r.run()
        gemini_waits = [w for w in waits if w >= 20]
        self.assertEqual(len(gemini_waits), 2)
        self.assertGreater(gemini_waits[1], gemini_waits[0])

    def test_retry_delay_parse(self):
        self.assertEqual(core.retry_delay_seconds("'retryDelay': '12.5s'"), 12.5)
        self.assertEqual(core.retry_delay_seconds('"retryDelay": "40s"'), 40.0)
        self.assertIsNone(core.retry_delay_seconds("503 UNAVAILABLE"))


class CompactLinesTests(unittest.TestCase):
    def test_merges_into_blocks(self):
        lines = ["[00:00:01] hi", "[00:00:05] there", "[00:00:16] next", "[00:00:20] one", "[00:01:00] later"]
        self.assertEqual(core.compact_lines(lines, 15),
                         ["[00:00:01] hi there", "[00:00:16] next one", "[00:01:00] later"])

    def test_off_and_untimed_lines(self):
        lines = ["[00:00:01] a", "no time", "[00:00:02] b"]
        self.assertEqual(core.compact_lines(lines, 0), lines)
        self.assertEqual(core.compact_lines(lines, 15), lines)

    def test_saves_size(self):
        lines = [f"[{core.fmt_ts(t)}] word word word" for t in range(0, 3600, 3)]
        out = "\n".join(core.compact_lines(lines, 15))
        self.assertLess(len(out), 0.7 * len("\n".join(lines)))

    def test_summary_on_by_default(self):
        self.assertTrue(pb.DEFAULT_FORM["summary_on"])
        self.assertIn("One-line summary", pb.build_prompt({"find": "x"}))


class UsageTests(unittest.TestCase):
    def test_pacific_day_rolls_over_at_midnight_pacific(self):
        from datetime import datetime, timezone
        # 07:59 UTC on Nov 17 is still Nov 16 in California (PST, UTC-8); 08:00 UTC is Nov 17
        self.assertEqual(usage.pacific_today(datetime(2026, 11, 17, 7, 59, tzinfo=timezone.utc)), "2026-11-16")
        self.assertEqual(usage.pacific_today(datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)), "2026-11-17")
        # summer (PDT, UTC-7)
        self.assertEqual(usage.pacific_today(datetime(2026, 7, 1, 6, 59, tzinfo=timezone.utc)), "2026-06-30")

    def test_pacific_day_without_timezone_data(self):
        from datetime import datetime, timezone
        from unittest import mock
        import zoneinfo
        with mock.patch.object(zoneinfo, "ZoneInfo", side_effect=zoneinfo.ZoneInfoNotFoundError("x")):
            self.assertEqual(usage.pacific_today(datetime(2026, 11, 17, 7, 59, tzinfo=timezone.utc)), "2026-11-16")
            self.assertEqual(usage.pacific_today(datetime(2026, 7, 1, 6, 59, tzinfo=timezone.utc)), "2026-06-30")
            self.assertEqual(usage.pacific_today(datetime(2026, 7, 1, 7, 0, tzinfo=timezone.utc)), "2026-07-01")

    def test_count_reset_and_describe(self):
        from datetime import datetime, timezone
        day1 = datetime(2026, 11, 17, 20, 0, tzinfo=timezone.utc)
        day2 = datetime(2026, 11, 18, 20, 0, tzinfo=timezone.utc)
        s = {}
        self.assertIn("none yet", usage.describe(s, day1))
        usage.record(s, "flash", day1)
        usage.record(s, "flash", day1)
        usage.record(s, "lite", day1)
        usage.mark_out(s, "flash", day1)
        self.assertEqual(usage.describe(s, day1), "Gemini requests today: flash 2 (limit reached), lite 1")
        self.assertIn("none yet", usage.describe(s, day2))  # new Pacific day starts over

    def test_bad_stored_data_is_ignored(self):
        s = {"gemini_usage": {"date": usage.pacific_today(), "counts": {"a": "x", "b": 3}, "out": [1, "b"]}}
        self.assertEqual(usage.describe(s), "Gemini requests today: b 3 (limit reached)")

    def test_runner_reports_requests(self):
        events = []
        orig = core.generate
        core.generate = lambda *a: (_ for _ in ()).throw(RuntimeError(GeminiDailyLimitTests.DAY))
        try:
            with tempfile.TemporaryDirectory() as d:
                cfg = core.RunConfig(api_key="k", model="m", prompt="p", output_dir=d, backup_model="lite")
                vtt = Path(d, "x.vtt")
                vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhi\n", encoding="utf-8")
                core.Runner(cfg, "", lambda *a: None, lambda *a: None, lambda m: None, threading.Event(),
                            files=[str(vtt)], on_gemini=lambda e, m: events.append((e, m))).run()
        finally:
            core.generate = orig
        self.assertEqual(events, [("sent", "m"), ("out", "m"), ("sent", "lite"), ("out", "lite")])


class ResultToolsTests(unittest.TestCase):
    TEXT = ("Timestamps:\n\n00:00 – Intro\n\n00:12:05 – Bob – Christian – Length: ~6 minutes\n\n"
            "00:20:00 – Ann – Muslim\n\n1:05:09 – Cal\n")

    def test_entries_and_seconds(self):
        self.assertEqual([s for s, _ in core.result_entries(self.TEXT)], [0, 725, 1200, 3909])
        self.assertEqual(core.ts_seconds("1:05:09"), 3909)
        self.assertEqual(core.ts_seconds("05:09"), 309)

    def test_checks(self):
        self.assertEqual(core.check_result(self.TEXT, 7200), [])
        bad = "00:00 – Intro\n00:10:00 – A\n00:09:00 – B\n00:09:05 – C\n"
        w = core.check_result(bad, 300)
        self.assertTrue(any("out of order" in x for x in w))
        self.assertTrue(any("10 seconds" in x for x in w))
        self.assertTrue(any("past the end" in x for x in w))
        self.assertTrue(any("fewer than 3" in x for x in core.check_result("00:00 – Intro\n01:00 – A\n")))
        self.assertTrue(any("characters" in x for x in core.check_result("00:00 x\n" + "y" * 5001)))
        self.assertEqual(core.check_result("No qualifying segments found."), [])

    def test_clip_rows_and_csv(self):
        rows = core.clip_rows(self.TEXT, "abcdefghijk", 4000)
        self.assertEqual([(r["start"], r["end"]) for r in rows],
                         [("00:12:05", "00:18:05"), ("00:20:00", "01:05:09"), ("01:05:09", "01:06:40")])
        self.assertEqual(rows[0]["link"], "https://www.youtube.com/watch?v=abcdefghijk&t=725s")
        self.assertEqual(core.clip_rows(self.TEXT)[0]["link"], "")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "c.csv")
            core.write_clip_csv(p, [{"start": "00:00:01", "end": "", "minutes": "", "description": "=HYPERLINK(1)",
                                     "link": ""}])
            text = p.read_text(encoding="utf-8-sig")
        self.assertIn("'=HYPERLINK(1)", text)  # never runs as a formula in Excel
        self.assertTrue(text.startswith("start,end,minutes,description,link"))

    def test_video_id_from_result_name(self):
        self.assertEqual(core.video_id_from_result("C:/x/My Stream [abcdefghijk].txt"), "abcdefghijk")
        self.assertEqual(core.video_id_from_result("My Stream.txt"), "")

    def test_whisper_formats(self):
        self.assertEqual(core.parse_caption_text('{"segments": [{"start": 61.2, "text": " hi"}]}', ".json"),
                         ["[00:01:01] hi"])
        self.assertEqual(core.parse_caption_text("start\tend\ttext\n62000\t63000\tworld\n", ".tsv"),
                         ["[00:01:02] world"])
        self.assertEqual(core.parse_caption_text("not json {", ".json"), [])


class WorklistTests(unittest.TestCase):
    T0 = 1_800_000_000.0  # a fixed moment

    def test_waiting_is_checked_hourly_and_dropped_after_two_days(self):
        s = {}
        self.assertTrue(worklist.add_waiting(s, "aaaaaaaaaaa", "Stream A", self.T0))
        self.assertFalse(worklist.add_waiting(s, "aaaaaaaaaaa", "", self.T0))  # no duplicates
        self.assertEqual(worklist.due_waiting(s, self.T0 + 60), [])
        self.assertEqual(worklist.due_waiting(s, self.T0 + 3600), ["aaaaaaaaaaa"])
        worklist.check_all_now(s)
        self.assertEqual(worklist.due_waiting(s, self.T0), ["aaaaaaaaaaa"])
        self.assertEqual(worklist.expire_waiting(s, self.T0 + 47 * 3600), [])
        self.assertEqual(len(worklist.expire_waiting(s, self.T0 + 49 * 3600)), 1)
        self.assertEqual(worklist.waiting(s), [])

    def test_backlog_per_day_and_waiting_first(self):
        s = {"backlog_per_day": 2}
        vids = [(c * 11, f"Old {c}") for c in "bcdef"]
        self.assertEqual(worklist.add_backlog(s, "https://www.youtube.com/@x/streams", "@x", vids,
                                              skip_ids=["ccccccccccc"], now=self.T0), 4)
        worklist.add_waiting(s, "aaaaaaaaaaa", "New", self.T0 - 7200)
        plan = worklist.plan_auto_run(s, 20, self.T0)
        self.assertEqual(plan, ["aaaaaaaaaaa", "bbbbbbbbbbb", "ddddddddddd"])  # new stream first, then 2 old
        worklist.apply_outcomes(s, {"aaaaaaaaaaa": "done", "bbbbbbbbbbb": "done", "ddddddddddd": "failed"},
                                now=self.T0)
        self.assertEqual(worklist.waiting(s), [])
        b = worklist.backlog(s)[0]
        self.assertEqual((b["done"], b["failed"], len(b["pending"])), (1, 1, 2))
        self.assertEqual(worklist.plan_auto_run(s, 20, self.T0 + 60), [])  # today's 2 are used up
        tomorrow = self.T0 + 24 * 3600
        self.assertEqual(worklist.plan_auto_run(s, 20, tomorrow), ["eeeeeeeeeee", "fffffffffff"])

    def test_still_waiting_and_daily_limit_pause(self):
        s = {}
        worklist.add_waiting(s, "aaaaaaaaaaa", "A", self.T0)
        worklist.apply_outcomes(s, {"aaaaaaaaaaa": "waiting", "zzzzzzzzzzz": "waiting"},
                                {"zzzzzzzzzzz": "Live now"}, now=self.T0 + 3600)
        self.assertEqual([w["id"] for w in worklist.waiting(s)], ["aaaaaaaaaaa", "zzzzzzzzzzz"])
        self.assertEqual(worklist.waiting(s)[1]["title"], "Live now")
        worklist.check_all_now(s)
        worklist.pause_today(s, self.T0)
        self.assertEqual(worklist.plan_auto_run(s, 20, self.T0), [])
        self.assertTrue(worklist.plan_auto_run(s, 20, self.T0 + 24 * 3600))

    def test_old_backlog_stream_without_captions_is_not_retried_forever(self):
        s = {}
        worklist.add_backlog(s, "u", "u", [("bbbbbbbbbbb", "Old")], now=self.T0)
        worklist.apply_outcomes(s, {"bbbbbbbbbbb": "waiting"}, now=self.T0)
        self.assertEqual(worklist.waiting(s), [])
        self.assertEqual(worklist.backlog(s)[0]["failed"], 1)

    def test_run_cap_respected(self):
        s = {"backlog_per_day": 50}
        worklist.add_backlog(s, "u", "u", [(f"{i:011d}", "") for i in range(30)], now=self.T0)
        self.assertEqual(len(worklist.plan_auto_run(s, 20, self.T0)), 20)


class StopTests(FakeRunTests):
    def test_stop_during_a_slow_gemini_answer_is_quick(self):
        import time as _t
        release = threading.Event()

        def slow(*a):
            release.wait(30)  # a Gemini answer that takes ages
            return "late"
        core.generate = slow
        r = self.make("aaaaaaaaaaa")
        threading.Timer(0.5, r.stop.set).start()
        t0 = _t.time()
        sm = r.run()
        release.set()
        self.assertTrue(sm["stopped"])
        self.assertLess(_t.time() - t0, 3)
        self.assertEqual(sm["saved"], [])

    def test_stop_during_a_slow_youtube_page_is_quick(self):
        import time as _t
        release = threading.Event()
        r = self.make("aaaaaaaaaaa")
        r._fetch_info = lambda url: release.wait(30) or {}
        threading.Timer(0.5, r.stop.set).start()
        t0 = _t.time()
        self.assertTrue(r.run()["stopped"])
        release.set()
        self.assertLess(_t.time() - t0, 3)


class ModelProblemTests(FakeRunTests):
    BUSY = "503 UNAVAILABLE. The model is overloaded."

    def busy_main(self):
        calls = []

        def gen(key, model, system, transcript):
            calls.append(model)
            if model == "m":
                raise RuntimeError(self.BUSY)
            return "ok text"
        core.generate = gen
        return calls

    def test_ask_and_pick_another_model(self):
        calls = self.busy_main()
        asked = []
        r = self.make("aaaaaaaaaaa", problem_choice="ask", backup_model="b2", model_choices=("m", "b2", "b3"))
        r._ask_problem = lambda kind, model, options: asked.append((kind, model, options)) or ("model", "b3")
        sm = r.run()
        self.assertEqual(sm["done"], 1)
        self.assertEqual(asked, [("busy", "m", ["b2", "b3"])])  # the busy model isn't offered
        self.assertEqual(calls[-1], "b3")

    def test_ask_and_wait(self):
        self.busy_main()
        r = self.make("aaaaaaaaaaa", problem_choice="ask", backup_model="b2")
        r._ask_problem = lambda kind, model, options: ("wait", "")
        sm = r.run()
        self.assertEqual((sm["done"], sm["waiting"], sm["gemini_wait"]), (0, 1, "busy"))

    def test_ask_and_cancel(self):
        self.busy_main()
        r = self.make("aaaaaaaaaaa", problem_choice="ask", backup_model="b2")
        r._ask_problem = lambda kind, model, options: ("cancel", "")
        sm = r.run()
        self.assertTrue(sm["stopped"])
        self.assertEqual(sm["waiting"], 0)

    def test_nobody_to_ask_means_wait(self):
        calls = self.busy_main()
        sm = self.make("aaaaaaaaaaa", problem_choice="ask", backup_model="b2").run()  # automatic run: no asker
        self.assertEqual((sm["waiting"], sm["gemini_wait"]), (1, "busy"))
        self.assertNotIn("b2", calls)  # never silently uses another model

    def test_cooling_or_out_models_get_no_request(self):
        calls = []
        core.generate = lambda key, model, system, transcript: calls.append(model) or "ok text"
        import time as _t
        sm = self.make("aaaaaaaaaaa", problem_choice="backup", backup_model="b2",
                       cooloff={"m": _t.time() + 600}).run()
        self.assertEqual((sm["done"], calls), (1, ["b2"]))
        calls.clear()
        sm = self.make("aaaaaaaaaaa", problem_choice="wait", out_today=("m",), skip_existing=False).run()
        self.assertEqual((calls, sm["gemini_wait"]), ([], "out"))


class PlanSplitTests(unittest.TestCase):
    def test_waiting_first_and_each_list_needs_its_model(self):
        s = {"backlog_per_day": 3}
        worklist.add_backlog(s, "u", "u", [("bbbbbbbbbbb", ""), ("ccccccccccc", "")])
        self.assertEqual(worklist.plan_split(s), ([], ["bbbbbbbbbbb", "ccccccccccc"]))
        worklist.add_waiting(s, "aaaaaaaaaaa", "new")
        worklist.check_all_now(s)
        self.assertEqual(worklist.plan_split(s), (["aaaaaaaaaaa"], []))  # new streams first, on their own
        # main model out for today: the backlog (its own model) still goes ahead
        self.assertEqual(worklist.plan_split(s, waiting_ok=False), ([], ["bbbbbbbbbbb", "ccccccccccc"]))
        self.assertEqual(worklist.plan_split(s, waiting_ok=False, backlog_ok=False), ([], []))


class LiteHelperTests(unittest.TestCase):
    """Flash-Lite helper: segments as data, tidied and laid out by the app (cases from a real Lite answer)."""
    FORM = {"find": "a caller argues against the host", "min_minutes": "4", "detail_on": True,
            "detail_label": "Stance", "summary_on": True, "trailing_period": True}
    # what Gemini 3.5 Flash-Lite actually got wrong: Chaya split into 3 overlapping pieces, a too-long Katie
    LITE = {"segments": [
        {"start": "00:27:58", "end": "01:10:30", "name": "James", "detail": "Christian", "summary": "Motion."},
        {"start": "01:11:34", "end": "01:29:50", "name": "Katie", "detail": "Christian", "summary": "Water."},
        {"start": "02:22:30", "end": "02:52:30", "name": "Kaya", "detail": "Jewish", "summary": "Slavery."},
        {"start": "02:46:58", "end": "02:52:58", "name": "Kaya", "detail": "Jewish", "summary": "More."},
        {"start": "02:49:54", "end": "02:54:20", "name": "kaya ", "detail": "Jewish", "summary": "Talmud."},
        {"start": "02:58:00", "end": "03:00:00", "name": "Brief", "detail": "", "summary": "Too short."},
    ]}

    def test_parse_and_tidy(self):
        segs = core.parse_segments("```json\n" + json.dumps(self.LITE) + "\n```")
        self.assertEqual(len(segs), 6)
        tidy = core.tidy_segments(segs, 4, duration=4 * 3600)
        self.assertEqual([(core.fmt_ts(x["start"]), x["name"]) for x in tidy],
                         [("00:27:58", "James"), ("01:11:34", "Katie"), ("02:22:30", "Kaya")])
        kaya = tidy[2]
        self.assertEqual((kaya["end"] - kaya["start"]) // 60, 31)  # one call, 02:22:30 -> 02:54:20
        self.assertEqual(kaya["summary"], "Slavery.")  # from the longest piece

    def test_merge_only_close_pieces_of_the_same_name(self):
        segs = [{"start": 0, "end": 600, "name": "Ann", "detail": "", "summary": ""},
                {"start": 700, "end": 1200, "name": "Ann", "detail": "", "summary": ""},   # 100 s gap: merged
                {"start": 1500, "end": 2000, "name": "Ann", "detail": "", "summary": ""},  # 300 s gap: separate
                {"start": 2050, "end": 2600, "name": "Bob", "detail": "", "summary": ""}]  # other name: separate
        tidy = core.tidy_segments(segs)
        self.assertEqual([(x["name"], x["start"], x["end"]) for x in tidy],
                         [("Ann", 10, 1200), ("Ann", 1500, 2000), ("Bob", 2050, 2600)])

    def test_render_in_the_chosen_format(self):
        tidy = core.tidy_segments(core.parse_segments(json.dumps(self.LITE)), 4)
        text = pb.render_segments(self.FORM, tidy)
        self.assertTrue(text.startswith("Timestamps:\n\n00:00 – Intro\n\n00:27:58 – James – Christian – "
                                        "Length: ~43 minutes – Motion.\n\n"))
        self.assertIn("01:11:34 – Katie – Christian – Length: ~18 minutes – Water.", text)
        self.assertTrue(text.rstrip().endswith("."))
        self.assertEqual(core.check_result(text, 4 * 3600), [])
        self.assertEqual(pb.render_segments(self.FORM, []), "No qualifying segments found.\n")

    def test_bad_json(self):
        self.assertIsNone(core.parse_segments("Sorry, I can't do that."))
        self.assertEqual(core.parse_segments('{"segments": [{"start": "x", "end": "1:00", "name": "A"}]}'), [])

    def test_data_prompt_has_the_length_rule(self):
        p = pb.build_data_prompt(self.FORM)
        self.assertIn("when that person leaves", p)
        self.assertIn("Never split one call", p)
        self.assertIn("{min_minutes}", p)


class LiteHelperRunTests(FakeRunTests):
    def make_lite(self, answers, **kw):
        sent = []

        def fake_json(key, model, system, contents, thinking=True):
            sent.append((model, "DRAFT LIST" in contents, thinking))
            a = answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a
        core.generate_json, self._orig_json = fake_json, core.generate_json
        self.addCleanup(lambda: setattr(core, "generate_json", self._orig_json))
        r = self.make("aaaaaaaaaaa", form=LiteHelperTests.FORM, **kw)
        r.cfg.model = r._model = "gemini-3.5-flash-lite"
        long_stream = {"title": "Long stream", "duration": 4 * 3600,
                       "automatic_captions": {"en": [{"ext": "json3", "url": "u"}]}}
        r._fetch_info = lambda url: long_stream
        return r, sent

    def test_two_passes_then_tidy(self):
        first = json.dumps(LiteHelperTests.LITE)
        checked = json.dumps({"segments": LiteHelperTests.LITE["segments"][:3]})
        r, sent = self.make_lite([first, checked])
        sm = r.run()
        self.assertEqual(sm["done"], 1, self.logs)
        self.assertEqual([(m, d) for m, d, _ in sent], [("gemini-3.5-flash-lite", False), ("gemini-3.5-flash-lite", True)])
        text = Path(sm["saved"][0]).read_text(encoding="utf-8")
        self.assertIn("02:22:30 – Kaya – Jewish – Length: ~30 minutes", text)
        self.assertEqual(text.count("Kaya"), 1)
        self.assertEqual(self.calls, [])  # the normal text prompt wasn't used

    def test_check_step_failing_keeps_the_first_list(self):
        r, sent = self.make_lite([json.dumps(LiteHelperTests.LITE), "not json"])
        sm = r.run()
        self.assertEqual(sm["done"], 1)
        self.assertIn("Kaya", Path(sm["saved"][0]).read_text(encoding="utf-8"))

    def test_unreadable_answer_falls_back_to_the_normal_prompt(self):
        r, sent = self.make_lite(["I'm not JSON"])
        sm = r.run()
        self.assertEqual(sm["done"], 1)
        self.assertEqual(len(self.calls), 1)  # the normal text prompt was used instead

    def test_model_without_thinking_setting(self):
        refusal = RuntimeError("400 INVALID_ARGUMENT. Thinking level is not supported for this model.")
        r, sent = self.make_lite([refusal, json.dumps(LiteHelperTests.LITE), json.dumps(LiteHelperTests.LITE)])
        self.assertEqual(r.run()["done"], 1, self.logs)
        self.assertEqual([t for _, _, t in sent], [True, False, False])  # asked once more without it, then never

    def test_strong_models_and_hand_edited_prompts_skip_the_helper(self):
        r, sent = self.make_lite([])
        r.cfg.model = r._model = "gemini-4-hydrogen"
        r.run()
        self.assertEqual(sent, [])
        r2, sent2 = self.make_lite([], skip_existing=False)
        r2.cfg.form = None
        r2.run()
        self.assertEqual(sent2, [])


class HourTypoTests(unittest.TestCase):
    """Real case from 3.5 Flash-Lite: James's call (00:27:43 to about 01:10) came back starting at 02:27:43."""
    LINES = ["[00:27:40] alright next up we have James on the line", "[00:28:00] hi James go ahead",
             "[01:10:20] thanks James take care", "[02:22:46] Kaya is on the line", "[02:27:43] so Kaya says"]

    def segs(self, start, end, name="James"):
        return core.parse_segments(json.dumps({"segments": [{"start": start, "end": end, "name": name}]}))

    def test_end_before_start_is_repaired_using_the_transcript(self):
        s = self.segs("02:27:43", "01:10:30")
        notes = core.repair_hour_typos(s, self.LINES, 4 * 3600)
        self.assertEqual((core.fmt_ts(s[0]["start"]), core.fmt_ts(s[0]["end"])), ("00:27:43", "01:10:30"))
        self.assertTrue(notes and "James" in notes[0])

    def test_start_after_the_stream_ends(self):
        s = self.segs("05:27:43", "", "James")
        core.repair_hour_typos(s, self.LINES, 4 * 3600)
        self.assertEqual(core.fmt_ts(s[0]["start"]), "00:27:43")  # the hour where James is mentioned

    def test_good_segments_are_left_alone(self):
        s = self.segs("02:22:46", "02:52:10", "Kaya")
        self.assertEqual(core.repair_hour_typos(s, self.LINES, 4 * 3600), [])
        self.assertEqual(core.fmt_ts(s[0]["start"]), "02:22:46")

    def test_whole_flow_puts_james_back(self):
        lite = {"segments": [{"start": "02:27:43", "end": "01:10:30", "name": "James"},
                             {"start": "01:11:47", "end": "01:29:50", "name": "Katie"}]}
        s = core.parse_segments(json.dumps(lite))
        core.repair_hour_typos(s, self.LINES, 4 * 3600)
        tidy = core.tidy_segments(s, 4, 4 * 3600)
        text = pb.render_segments({"find": "x", "detail_on": False, "summary_on": False}, tidy)
        self.assertIn("00:27:43 – James – Length: ~43 minutes", text)
        self.assertLess(text.index("James"), text.index("Katie"))


class OverlapTests(unittest.TestCase):
    """Real case (YouTube captions + 3.5 Flash-Lite): Steven at 02:48:54 for ~22 min, but Kaya's call ran until
    about 02:53:34 and Steven was actually introduced at about 02:54:5x."""
    LINES = ["[02:23:30] next we have Kaya", "[02:48:50] so Kaya you were saying",
             "[02:53:30] thanks Kaya take care", "[02:54:50] alright Steven you're on the air",
             "[02:55:10] Steven go ahead"]

    def segs(self):
        return core.parse_segments(json.dumps({"segments": [
            {"start": "02:23:34", "end": "02:53:34", "name": "Kaya"},
            {"start": "02:48:54", "end": "03:10:54", "name": "Steven"}]}))

    def test_overlap_fixed_from_where_the_name_comes_up(self):
        s = self.segs()
        fixes, warnings = core.repair_overlaps(s, self.LINES)
        self.assertEqual((fixes and "Steven" in fixes[0], warnings), (True, []))
        tidy = core.tidy_segments(s, 4)
        text = pb.render_segments({"find": "x", "detail_on": False, "summary_on": False}, tidy)
        self.assertIn("02:54:50 – Steven – Length: ~16 minutes", text)
        self.assertIn("02:23:34 – Kaya – Length: ~30 minutes", text)

    def test_name_not_found_means_a_warning_not_a_guess(self):
        s = self.segs()
        fixes, warnings = core.repair_overlaps(s, ["[02:54:50] next caller go ahead"])
        self.assertEqual(fixes, [])
        self.assertTrue(warnings and "overlap" in warnings[0])
        self.assertEqual(core.fmt_ts(s[1]["start"]), "02:48:54")  # left alone

    def test_back_to_back_calls_are_fine(self):
        s = core.parse_segments(json.dumps({"segments": [
            {"start": "01:00:00", "end": "01:20:00", "name": "A"}, {"start": "01:19:45", "end": "01:30:00", "name": "B"}]}))
        self.assertEqual(core.repair_overlaps(s, []), ([], []))  # 15 s is within the slack

    def test_check_result_warns_about_overlaps_from_any_model(self):
        text = "00:00 – Intro\n02:23:34 – Kaya – Length: ~30 minutes\n02:48:54 – Steven – Length: ~22 minutes\n"
        self.assertTrue(any("runs past the next one" in w for w in core.check_result(text)))
        ok = "00:00 – Intro\n02:23:34 – Kaya – Length: ~30 minutes\n02:54:54 – Steven – Length: ~16 minutes\n"
        self.assertFalse(any("runs past" in w for w in core.check_result(ok)))

    def test_whole_call_with_the_wrong_hour_is_moved(self):
        # real case: James given as 02:27:44 for ~43 min (start AND end two hours late), colliding with Khaya
        lines = ["[00:27:40] next up James", "[00:28:00] hi James", "[02:23:45] Khaya is on the line"]
        s = core.parse_segments(json.dumps({"segments": [
            {"start": "02:23:49", "end": "02:54:49", "name": "Khaya"},
            {"start": "02:27:44", "end": "03:10:44", "name": "James"}]}))
        fixes, warnings = core.repair_overlaps(s, lines)
        self.assertEqual(warnings, [])
        self.assertTrue(fixes and "James" in fixes[0] and "00:27:44" in fixes[0])
        tidy = core.tidy_segments(s, 4)
        self.assertEqual([(core.fmt_ts(x["start"]), x["name"], (x["end"] - x["start"]) // 60) for x in tidy],
                         [("00:27:44", "James", 43), ("02:23:49", "Khaya", 31)])
