"""Headless smoke test of the GUI with fake tkinter/customtkinter. Usage: smoke.py <fakes_dir> <app_dir>"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

FAKES, APPDIR = sys.argv[1], sys.argv[2]
sys.path[:0] = [FAKES, APPDIR]
tmp = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "cfg")
os.environ["HOME"] = os.path.join(tmp, "home")
os.makedirs(os.path.join(tmp, "home", "Documents"))

import customtkinter as ctk  # fake
from tkinter import filedialog, messagebox  # fake

import core
import settings


def pump(cond=lambda: False, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        ctk.run_after(limit=50)
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def check(c, msg):
    if not c:
        raise AssertionError(msg)
    print("  ok -", msg)


# fakes for the network pieces
core.test_api_key = lambda key, model: (True, "Works.") if key == "GOODKEY" else (False, "Gemini rejected this API key.")
core.generate = lambda *a: "Timestamps:\n\n00:00:05 – Caller – Christian – Length: ~5 minutes\n"
core.download_text = lambda url, retries=3, stop=None: json.dumps(
    {"events": [{"tStartMs": 5000, "segs": [{"utf8": "hello"}]}]})
FETCHED = []


def fake_fetch(self, url):
    FETCHED.append(url)
    if "blockblockb" in url:
        raise RuntimeError("ERROR: [youtube] x: Sign in to confirm you're not a bot.")
    return {"title": "Bible Guy Debates | 05/12/26", "duration": 7200,
            "automatic_captions": {"en-orig": [{"ext": "json3", "url": "https://yt/timedtext?lang=en"}]}}


core.Runner._fetch_info = fake_fetch
_orig_sleep = core.Runner._sleep
core.Runner._sleep = lambda self, s: self._check_stop()

import updates
UPD = {"result": ("current", None)}
updates.check = lambda repo, cur: UPD["result"]

import app as appmod
appmod.GITHUB_REPO = "tester/stream-timestamper"  # as if built by GitHub

print("1. first launch")
a = appmod.App()
import ui_common as ui
check(os.path.exists(ui.ICON_ICO) and ui.icon_image(44) is not None, "icon files found and sidebar image loads")
check(not hasattr(a, "side_safe"), "no 'YouTube: no login used' line in the sidebar")
pump(lambda: a.wizard is not None)
w = a.wizard
check(w is not None and w._key() == "welcome", "wizard opens at Welcome")
check([k for k, _ in w.steps] == ["welcome", "gemini", "safety", "output", "tour"], "no cookies step")
w.next_btn.invoke()
a.v_key.set("BADKEY")
w.next_btn.invoke()
pump(lambda: not w._testing)
check(w._key() == "gemini", "bad key blocked")
a.v_key.set("GOODKEY")
w.next_btn.invoke()
pump(lambda: w._key() == "safety")
check(w._key() == "safety", "-> safety step")
w.next_btn.invoke()
check(w._key() == "output", "-> output step")
w.next_btn.invoke()
w.next_btn.invoke()
check(a.wizard is None and a.s["setup_done"], "wizard finished")
check(pump(lambda: a.s["last_update_check"] > 0), "startup update check ran (up to date)")
check(not a.banner.gridded, "no update banner when up to date")

print("1b. model notes")
check(a.s["model"] == "gemini-3.6-flash", "default model is 3.6 Flash")
notes = [l for l in a._model_notes if l.winfo_exists()]
check(notes and "Recommended" in notes[0].kw["text"], "model note under the model box")
a.v_model.set("gemini-4-argon")
check("Paid keys only" in notes[0].kw["text"], "note follows the chosen model")
a.v_model.set("my-custom-model")
check("Custom model" in notes[0].kw["text"], "custom model name gets a generic note")
a.v_model.set("gemini-3.6-flash")

print("2. prompt chapters option")
a.fv["chapters"].set(True)
check("00:00 – Intro" in a.preview_box.buf, "chapters line in preview")
a.fv["chapters"].set(False)

print("3. run with a link: logged out")
a.show_page("run")
a.urls_box.buf = "https://www.youtube.com/live/WbPksTZLcRA?si=abc"
a.v_dmin.set("0"); a.v_dmax.set("0")
a.start()
pump(lambda: a.btn_start.kw.get("state") == "normal", timeout=10)
check("1 saved" in a.status_lbl.kw["text"], "link run saved 1: " + a.status_lbl.kw["text"])
check(FETCHED, "YouTube was queried for the link")

print("3b. layout and logs")
from types import SimpleNamespace as _Ev
a._on_split_configure()
check(a._split_y() == 300 + 4, "handle starts just below the links card")
a._split_press(_Ev(y_root=500))
a._split_drag(_Ev(y_root=436))
check(a._split_y() == 240, "dragging the handle resizes the links area")
a._on_split_moved()
check(a.s["run_split"] == 0.4, "dragged handle position remembered")
a._split_drag(_Ev(y_root=-5000))
check(a._split_y() == 64, "can't drag the links area smaller than its minimum")
a._split_drag(_Ev(y_root=5000))
check(a._split_y() == 600 - 226, "can't squeeze the Activity area below its minimum")
a._split_toggle()
check(a._split_y() == 64, "double-click shrinks the links area")
a._split_toggle()
check(a._split_y() == 600 - 226, "double-click again brings it back")
a._place_split(240)
a._on_split_moved()
import runlog
logs = sorted(runlog.log_dir().glob("run-*.txt"))
check(len(logs) == 1 and "1 saved" in logs[0].read_text(encoding="utf-8"), "run log file written")
check("GOODKEY" not in logs[0].read_text(encoding="utf-8"), "API key not in the log file")
a.open_log_window()
lw = a.log_window
check(lw is not None and "1 saved" in lw.box.buf, "pop-out window shows the current log")
a._append_log("live line", "info")
check("live line" in lw.box.buf and "live line" in a.log_box.buf, "pop-out updates live")
a.open_log_window()
check(a.log_window is lw, "second click reuses the open window")
orig_place = a._place_split
moves = []
a._place_split = lambda y: (moves.append(y), orig_place(y))
a.urls_box.buf = "https://www.youtube.com/live/WbPksTZLcRA"
a.v_skip.set(False)
a.start()
pump(lambda: a.btn_start.kw.get("state") == "normal", timeout=10)
check(moves[:1] == [64] and moves[-1] == 240, "links shrink during a run and come back after: %r" % moves)
a._place_split = orig_place
a.v_skip.set(True)
check(len(list(runlog.log_dir().glob("run-*.txt"))) == 2, "one log file per run")

print("4. caption files")
vtt = Path(tmp, "Stream 12.vtt")
vtt.write_text("WEBVTT\n\n00:01:00.000 --> 00:01:02.000\ncaller joins\n", encoding="utf-8")
filedialog.RETURN["askopenfilenames"] = (str(vtt),)
a.add_caption_files()
check("1 file" in a.files_lbl.kw["text"], "file listed")
a.urls_box.buf = ""
FETCHED.clear()
a.start()
pump(lambda: a.btn_start.kw.get("state") == "normal", timeout=10)
check("1 saved" in a.status_lbl.kw["text"] and not FETCHED, "file run saved without touching YouTube")
a.clear_caption_files()

print("5. YouTube block -> stop, banner, cooldown")
a.urls_box.buf = "blockblockb\naaaaaaaaaaa"
FETCHED.clear()
messagebox.CALLS.clear()
a.start()
pump(lambda: a.btn_start.kw.get("state") == "normal", timeout=10)
check(len(FETCHED) == 1, "stopped after first block (no retries)")
check(any(k == "showwarning" for k, _ in messagebox.CALLS), "warning dialog shown")
check(a.banner.gridded and "isn't involved" in a.banner_lbl.kw["text"], "banner explains + offers caption files")
check(a.s["last_blocked"] > 0, "block time saved")
messagebox.ANSWERS["askyesno"] = False
a.urls_box.buf = "aaaaaaaaaaa"
FETCHED.clear()
a.start()
check(not FETCHED and (a.worker is None or not a.worker.is_alive()), "cool-down prompt: No = don't run")
messagebox.ANSWERS["askyesno"] = True

print("6. caption-files-only mode")
a.v_files_only.set(True)
a._refresh_files_only()
check(a.urls_box.kw.get("state") == "disabled", "links box disabled")
check(a.links_off_lbl.gridded, "Run page says links are turned off")
messagebox.CALLS.clear()
a.start()
check(messagebox.CALLS and "caption file" in messagebox.CALLS[-1][1], "asks for a caption file instead")
a.v_files_only.set(False)
a._refresh_files_only()

print("7. help window + settings")
a.show_caption_help()
a.v_max_run.set("abc")
messagebox.CALLS.clear()
check(a.collect() is None and messagebox.CALLS[-1][0] == "showerror", "bad number rejected")
a.v_max_run.set("15")

print("7b. tidier pages and the request counter")
check("requests today" in a.usage_lbl.kw["text"], "Run page shows today's Gemini requests: " + a.usage_lbl.kw["text"])
check(a.s["gemini_usage"]["counts"].get("gemini-3.6-flash", 0) >= 3, "runs and key tests were counted")
check(not a.preview_box.master.gridded, "AI instructions preview starts folded")
a._apply_state(a.get_form(), True, "hand edited")
check(a.preview_box.master.gridded, "advanced (hand-edited) prompt opens the preview")
a._apply_state(a.get_form(), False)
a.v_merge.set("90")
a.collect()
check(a.s["merge_seconds"] == 60, "merge block length capped at 60 s")
a.v_merge.set("15")

print("7c. results: clickable times and clip list")
import ui_common
opened = []
ui_common.open_url = opened.append
res = [p for p in a._result_paths if p.endswith("[WbPksTZLcRA].txt")] if a._result_paths else []
a.show_page("results")
res = [p for p in a._result_paths if p.endswith("[WbPksTZLcRA].txt")]
check(res, "YouTube result is listed")
a.select_result(res[0])
vb = a.view_box
check(vb.tags.get("ts_link") == [("3.0", "3.8")], "the start time on the result line is a link: %r" % vb.tags.get("ts_link"))
vb.tag_binds[("ts0", "<Button-1>")](None)
check(opened == ["https://www.youtube.com/watch?v=WbPksTZLcRA&t=5s"], "clicking it opens the stream at that moment")
messagebox.ANSWERS["askyesno"] = False
a.save_clip_list()
csvs = list(Path(a.s["output_dir"]).glob("*clips.csv"))
check(len(csvs) == 1 and "t=5s" in csvs[0].read_text(encoding="utf-8-sig"), "clip list saved with links")
messagebox.ANSWERS["askyesno"] = True

print("7d. queue page and automatic runs")
import worklist
a.show_page("queue")
check("YouTube limited" in a.queue_status.kw["text"], "queue pauses after a YouTube block")
a.s["last_blocked"] = 0
a.refresh_queue()
check("On:" in a.queue_status.kw["text"], "queue page says automatic runs are on: " + a.queue_status.kw["text"])
worklist.add_waiting(a.s, "ccccccccccc", "Still live stream", time.time() - 7200)
worklist.check_all_now(a.s)
FETCHED.clear()
check(a._maybe_auto_run() is True, "a due waiting stream starts an automatic run")
pump(lambda: a.btn_start.kw.get("state") == "normal", timeout=10)
check(FETCHED and "ccccccccccc" in FETCHED[0], "the automatic run fetched the waiting stream")
check(worklist.waiting(a.s) == [], "done streams leave the waiting list")
check("Automatic run" in a.status_lbl.kw["text"], "status says it was automatic: " + a.status_lbl.kw["text"])
check(a._maybe_auto_run() is False, "nothing more to do: no run")
a.v_auto.set(False)
worklist.add_waiting(a.s, "ddddddddddd", "x", time.time() - 7200)
worklist.check_all_now(a.s)
check(a._maybe_auto_run() is False, "switched off: no automatic run")
a.v_auto.set(True)
a.s["waiting"] = []

print("7e. transcribe button without Whisper installed")
import transcribe as _tr
if not _tr.available():
    messagebox.CALLS.clear()
    a.transcribe_recording()
    check(messagebox.CALLS and "isn't included" in messagebox.CALLS[-1][1], "explains when Whisper isn't in this copy")

print("7f. transcribe prompt and its tick box")
asked = []
a._ask_choice = lambda title, msg, options, check=None: asked.append((msg, [k for k, _, _ in options], check)) or "cancel"
a.urls_box.buf = ""
a.transcribe_recording() if _tr.available() else None
a.urls_box.buf = "https://youtu.be/aaaaaaaaaaa"
if _tr.available():
    a.transcribe_recording()
    check(asked[-1][1] == ["links", "file", "cancel"], "links in the box: offers them, a file, or cancel")
    check(asked[0][1] == ["file", "cancel"], "no links: choose a file or cancel")
    check(asked[-1][2][1] is a.v_then_ts and a.v_then_ts.get(), "'also find the timestamps' is ticked by default")
check(a.s.get("transcribe_then_timestamps", True) is True, "setting defaults to doing everything in one go")
a.v_then_ts.set(False)
a.collect()
check(a.s["transcribe_then_timestamps"] is False, "unticking it is remembered")
a.v_then_ts.set(True)
a.urls_box.buf = ""

print("8. relaunch")
a._on_close()
saved = json.loads(settings.settings_path().read_text())
check(saved["max_per_run"] == 15, "settings saved")
b = appmod.App()
pump()
check(b.wizard is None and not b.banner.gridded, "no wizard or banner on second launch")
print("9. update notice")
UPD["result"] = ("newer", "9.9.9")
messagebox.ANSWERS["askyesno"] = False
messagebox.CALLS.clear()
b.check_updates(manual=True)
check(pump(lambda: b.s["update_available"] == "9.9.9"), "manual check found 9.9.9")
check(b.banner.gridded and "9.9.9 is available" in b.banner_lbl.kw["text"], "update banner shown")
check(any(k == "askyesno" for k, _ in messagebox.CALLS), "asked whether to open the download page")
b.show_banner("YouTube is limiting requests from your internet connection", "error")
b._show_update_banner("9.9.9")
check("YouTube is limiting" in b.banner_lbl.kw["text"], "update notice doesn't cover a block warning")
b._on_close()
UPD["result"] = ("failed", None)
c = appmod.App()
pump(lambda: c.banner.gridded, timeout=3)
check(c.banner.gridded and "9.9.9" in c.banner_lbl.kw["text"], "remembered update shown on next launch")
c.v_check_updates.set(False)
c._on_close()
d = appmod.App()
pump(timeout=2)
check(not d.banner.gridded, "no notice when update checks are turned off")
appmod.GITHUB_REPO = ""
UPD["result"] = ("newer", "8.8.8")
e = appmod.App()
before = e.s["last_update_check"]
e.check_updates(manual=True)
pump(timeout=2)
check(e.s["last_update_check"] == before and e.s["update_available"] != "8.8.8",
      "local build (no repo): starts fine, never checks")
print("\nALL SMOKE CHECKS PASSED")
