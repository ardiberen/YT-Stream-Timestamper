"""Windows only: pasting in the app must give what was copied last anywhere, not what the app copied earlier.

Reproduces the bug where a YouTube link copied in the browser pasted into the links box as old timestamps
(Tk kept its own copy of what the app had copied). "Another program" is a real separate process here. Skipped on
other systems, and if the machine has no clipboard to use (some build servers)."""
import faulthandler
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
faulthandler.enable()  # if Windows/Tk ever crashes here, print where

ON_WINDOWS = sys.platform.startswith("win")

# a tiny separate program that copies text, the way a browser does
OTHER_PROGRAM = r'''
import ctypes, sys
from ctypes import wintypes
u = ctypes.WinDLL("user32"); k = ctypes.WinDLL("kernel32")
u.OpenClipboard.argtypes = [wintypes.HWND]; u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
u.SetClipboardData.restype = wintypes.HANDLE
k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]; k.GlobalAlloc.restype = wintypes.HGLOBAL
k.GlobalLock.argtypes = [wintypes.HGLOBAL]; k.GlobalLock.restype = wintypes.LPVOID
k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
data = (sys.argv[1] + "\0").encode("utf-16-le")
h = k.GlobalAlloc(2, len(data)); p = k.GlobalLock(h); ctypes.memmove(p, data, len(data)); k.GlobalUnlock(h)
for _ in range(50):
    if u.OpenClipboard(None): break
else: sys.exit(2)
u.EmptyClipboard(); ok = u.SetClipboardData(13, h); u.CloseClipboard()
sys.exit(0 if ok else 3)
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
        self.root.withdraw()
        self.ui.install_clipboard_fix(self.root)

    def tearDown(self):
        self.root.destroy()

    def copy_in_another_program(self, text):
        import time

        p = subprocess.Popen([sys.executable, "-c", OTHER_PROGRAM, text])
        end = time.time() + 30
        while p.poll() is None:  # keep handling window messages: Windows tells our window it lost the clipboard
            self.root.update()
            time.sleep(0.01)
            if time.time() > end:
                p.kill()
                self.fail("the other program didn't finish copying")
        if p.returncode == 2:
            self.skipTest("clipboard not available on this machine")
        self.assertEqual(p.returncode, 0)
        self.root.update()

    def paste_into(self, widget):
        widget.focus_force()
        widget.event_generate("<<Paste>>")
        self.root.update()

    def test_paste_gives_the_newest_copy_not_the_apps_old_one(self):
        import tkinter as tk

        self.root.clipboard_clear()  # the app copies timestamps (Copy all) ...
        self.root.clipboard_append("00:00 – Intro (old timestamps)")
        self.root.update()
        self.copy_in_another_program("https://www.youtube.com/watch?v=WbPksTZLcRA")  # ... then the browser copies
        box = tk.Text(self.root)
        box.pack()
        self.paste_into(box)
        self.assertEqual(box.get("1.0", "end-1c"), "https://www.youtube.com/watch?v=WbPksTZLcRA")

    def test_paste_of_the_apps_own_copy_still_works(self):
        import tkinter as tk

        self.copy_in_another_program("something older")
        self.root.clipboard_clear()
        self.root.clipboard_append("Copied in the app – ünïcode")
        self.root.update()
        box = tk.Text(self.root)
        box.pack()
        self.paste_into(box)
        self.assertEqual(box.get("1.0", "end-1c"), "Copied in the app – ünïcode")

    def test_paste_replaces_the_selection_in_an_entry(self):
        import tkinter as tk

        e = tk.Entry(self.root)
        e.pack()
        e.insert(0, "abc OLD xyz")
        e.selection_range(4, 7)
        e.icursor(7)
        self.copy_in_another_program("NEW")
        self.paste_into(e)
        self.assertEqual(e.get(), "abc NEW xyz")


if __name__ == "__main__":
    unittest.main()
