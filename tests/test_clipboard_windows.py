"""Windows only: pasting in the app must give what was copied last anywhere, not what the app copied earlier.

Reproduces the bug where a YouTube link copied in the browser pasted into the links box as old timestamps
(Tk kept its own copy of what the app had copied). Skipped on other systems, and if the machine has no
clipboard to use (some build servers)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ON_WINDOWS = sys.platform.startswith("win")


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
        ctypes, u, k = ui._win_clipboard_api()
        if not ui._win_open_clipboard(u):
            self.root.destroy()
            self.skipTest("clipboard not available on this machine")
        u.CloseClipboard()

    def tearDown(self):
        self.root.destroy()

    def copy_elsewhere(self, text):
        """Like copying in another program: straight onto the Windows clipboard."""
        self.ui.write_clipboard(self.root, text)

    def test_write_then_read(self):
        self.ui.write_clipboard(self.root, "héllo – wörld")
        self.assertEqual(self.ui.read_clipboard(self.root), "héllo – wörld")

    def test_paste_gives_the_newest_copy_not_the_apps_old_one(self):
        import tkinter as tk

        self.ui.install_clipboard_fix(self.root)
        # the app copies timestamps the old way (Tk holds its own copy) ...
        self.root.clipboard_clear()
        self.root.clipboard_append("00:00 – Intro (old timestamps)")
        self.root.update()
        # ... then a link is copied in the browser
        self.copy_elsewhere("https://www.youtube.com/watch?v=WbPksTZLcRA")
        box = tk.Text(self.root)
        box.pack()
        self.root.update()
        box.focus_force()
        box.event_generate("<<Paste>>")
        self.root.update()
        self.assertEqual(box.get("1.0", "end-1c"), "https://www.youtube.com/watch?v=WbPksTZLcRA")

    def test_paste_replaces_the_selection_in_an_entry(self):
        import tkinter as tk

        self.ui.install_clipboard_fix(self.root)
        e = tk.Entry(self.root)
        e.pack()
        e.insert(0, "abc OLD xyz")
        e.selection_range(4, 7)
        e.icursor(7)
        self.copy_elsewhere("NEW")
        e.event_generate("<<Paste>>")
        self.root.update()
        self.assertEqual(e.get(), "abc NEW xyz")


if __name__ == "__main__":
    unittest.main()
