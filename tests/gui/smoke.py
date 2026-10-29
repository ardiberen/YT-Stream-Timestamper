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
check("no login used" in a.side_safe.kw["text"], "sidebar shows no-login status")
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
sp = a.run_split
sp.binds["<Configure>"]()
check(sp.sash[0] == 300 + 14, "divider starts just below the links card")
sp.sash_place(0, 0, 240)
sp.binds["<ButtonRelease-1>"]()
check(a.s["run_split"] == 0.4, "dragged divider position remembered")
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
check("never contacted" in a.side_safe.kw["text"], "sidebar says never contacted")
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
