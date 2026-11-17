"""Windows only: copy and paste in the app go through the Windows clipboard, never Tk's.

Reproduces the bug where a YouTube link copied in the browser pasted into the links box as timestamps the app
had copied earlier. "Another program" is a real separate process here. Skipped on other systems, and if the
machine has no clipboard to use (some build servers)."""
import faulthandler
import os
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
faulthandler.enable()  # if Windows/Tk ever crashes here, print where

ON_WINDOWS = sys.platform.startswith("win")

# tiny separate programs that copy text (like a browser) or read it (like Notepad pasting)
_API = r'''
import ctypes, sys
from ctypes import wintypes
u = ctypes.WinDLL("user32"); k = ctypes.WinDLL("kernel32")
u.OpenClipboard.argtypes = [wintypes.HWND]
u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]; u.SetClipboardData.restype = wintypes.HANDLE
u.GetClipboardData.argtypes = [wintypes.UINT]; u.GetClipboardData.restype = wintypes.HANDLE
k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]; k.GlobalAlloc.restype = wintypes.HGLOBAL
k.GlobalLock.argtypes = [wintypes.HGLOBAL]; k.GlobalLock.restype = wintypes.LPVOID
k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
for _ in range(100):
    if u.OpenClipboard(None): break
else: sys.exit(2)
'''
COPY_PROGRAM = _API + r'''
data = (sys.argv[1] + "\0").encode("utf-16-le")
h = k.GlobalAlloc(2, len(data)); p = k.GlobalLock(h); ctypes.memmove(p, data, len(data)); k.GlobalUnlock(h)
u.EmptyClipboard(); ok = u.SetClipboardData(13, h); u.CloseClipboard()
sys.exit(0 if ok else 3)
'''
READ_PROGRAM = _API + r'''
h = u.GetClipboardData(13); text = ctypes.wstring_at(k.GlobalLock(h)) if h else ""
u.CloseClipboard()
sys.stdout.buffer.write(text.encode("utf-8"))
'''


@unittest.skipUnless(ON_WINDOWS, "Windows clipboard only")
class WindowsClipboardTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk

        import ui_common as ui
        self.ui = ui
        try:
            self.root = tk.Tk()
        except tk.TclError as e:
            self.skipTest(f"no display: {e}")
        self.root.geometry("300x200+0+0")
        self.root.update()
        self.ui.install_clipboard_fix(self.root)

    def tearDown(self):
        self.root.destroy()

    def other_program(self, code, *args):
        """Run a separate program while still handling this window's messages, as the app always does."""
        p = subprocess.Popen([sys.executable, "-c", code, *args], stdout=subprocess.PIPE)
        end = time.time() + 30
        while p.poll() is None:
            self.root.update()
            time.sleep(0.01)
            if time.time() > end:
                p.kill()
                self.fail("the other program didn't finish")
        if p.returncode == 2:
            p.stdout.close()
            self.skipTest("clipboard not available on this machine")
        self.assertEqual(p.returncode, 0)
        self.root.update()
        with p.stdout:
            return p.stdout.read().decode("utf-8")

    def text_box(self):
        import tkinter as tk

        box = tk.Text(self.root, height=3)
        box.pack()
        self.root.update()
        return box

    def do(self, widget, event):
        widget.focus_force()
        widget.event_generate(event)
        self.root.update()

    def test_1_app_copies_then_browser_copies_then_paste_gives_the_link(self):
        self.assertTrue(self.ui.write_clipboard(self.root, "00:00 – Intro (old timestamps)"))  # Copy all
        self.other_program(COPY_PROGRAM, "https://www.youtube.com/watch?v=WbPksTZLcRA")  # copied in the browser
        box = self.text_box()
        self.do(box, "<<Paste>>")
        self.assertEqual(box.get("1.0", "end-1c"), "https://www.youtube.com/watch?v=WbPksTZLcRA")

    def test_2_apps_copy_reaches_other_programs_and_pastes_back(self):
        self.other_program(COPY_PROGRAM, "something older")
        self.assertTrue(self.ui.write_clipboard(self.root, "Copied in the app – ünïcode"))
        self.assertEqual(self.other_program(READ_PROGRAM), "Copied in the app – ünïcode")  # e.g. into YouTube
        box = self.text_box()
        self.do(box, "<<Paste>>")
        self.assertEqual(box.get("1.0", "end-1c"), "Copied in the app – ünïcode")

    def test_3_ctrl_c_in_a_text_box(self):
        box = self.text_box()
        box.insert("1.0", "pick THIS part")
        box.tag_add("sel", "1.5", "1.9")
        self.do(box, "<<Copy>>")
        self.assertEqual(self.other_program(READ_PROGRAM), "THIS")

    def test_4_paste_replaces_the_selection_in_an_entry(self):
        import tkinter as tk

        e = tk.Entry(self.root)
        e.pack()
        self.root.update()
        e.insert(0, "abc OLD xyz")
        e.selection_range(4, 7)
        e.icursor(7)
        self.other_program(COPY_PROGRAM, "NEW")
        self.do(e, "<<Paste>>")
        self.assertEqual(e.get(), "abc NEW xyz")

    def test_5_cut_in_an_entry(self):
        import tkinter as tk

        e = tk.Entry(self.root)
        e.pack()
        self.root.update()
        e.insert(0, "keep CUTME keep")
        e.selection_range(5, 10)
        self.do(e, "<<Cut>>")
        self.assertEqual(e.get(), "keep  keep")
        self.assertEqual(self.other_program(READ_PROGRAM), "CUTME")


if __name__ == "__main__":
    unittest.main()
