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
        self.assertEqual(core.build_system_prompt("min {min_minutes} {x}", 4.0), "min 4 {x}")
        self.assertEqual(core.build_system_prompt("min {min_minutes}", 2.5), "min 2.5")

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
            self.assertEqual(s3["model"], "gemini-3.8-flash")
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
        self.assertEqual((sm["done"], sm["skipped"], sm["failed"]), (1, 2, 1), self.logs)
        out = Path(self.tmp.name, "Long stream [aaaaaaaaaaa].txt")
        self.assertTrue(out.exists())
        self.assertIn("00:00:05", out.read_text())
        model, system, transcript = self.calls[0]
        self.assertEqual(system, "find 4 min calls")
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
        f = self.pb.builtin_form("Call-in show (every caller)")
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


if __name__ == "__main__":
    unittest.main()
