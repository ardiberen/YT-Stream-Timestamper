"""Shared look-and-feel and small helpers for the GUI (colors, fonts, cards, opening files)."""
from __future__ import annotations

import os
import subprocess
import sys
import webbrowser

import customtkinter as ctk

from version import APP_VERSION

APP_NAME = "Stream Timestamper"
VERSION = APP_VERSION
MODELS = ["gemini-4-hydrogen", "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite",
          "gemini-3.1-flash-lite", "gemini-4-argon"]
LITE_MARK = "lite"  # model names containing this get the Flash-Lite helper (see core.is_lite_model)
# One-line notes shown under the model box. The free daily numbers are Google's free-tier limits when this
# version was made; Google changes them now and then (Google AI Studio shows the current ones for your key).
MODEL_NOTES = {
    "gemini-4-hydrogen": "Recommended. The most accurate free model in testing (finds calls and their lengths "
                         "best). Free keys: about 20 requests a day.",
    "gemini-3.8-flash": "Very accurate. Free keys: about 20 requests a day.",
    "gemini-3.6-flash": "Good and fast. Free keys: about 20 requests a day.",
    "gemini-3.5-flash-lite": "Many more free requests (about 500 a day) but less accurate; the app adds extra "
                             "checks for it. The default backup, and good for working through a backlog.",
    "gemini-3.1-flash-lite": "About 500 free requests a day, less accurate; the app adds extra checks for it.",
    "gemini-4-argon": "Paid keys only (doesn't work with free keys).",
}


def model_note(model: str) -> str:
    m = (model or "").strip()
    if not m:
        return "Pick a model."
    return MODEL_NOTES.get(m, "Custom model name. Check Google AI Studio for whether free keys can use it and "
                              "what its limits are.")

# (light, dark) pairs
SIDEBAR_BG = ("#e8ecf2", "#14161b")
CONTENT_BG = ("#f4f6f9", "#1a1d23")
CARD_BG = ("#ffffff", "#22262e")
CARD_BORDER = ("#dce1e9", "#2d323c")
TEXT = ("#1f2328", "#e6e8eb")
MUTED = ("#5b6472", "#8c95a3")
ACCENT = ("#2f6fe0", "#3b82f6")
ACCENT_HOVER = ("#2558b8", "#2f6fd6")
GRIP = ("#b7c0cd", "#4a5260")  # the Run page's drag handle
NAV_HOVER = ("#dbe1ea", "#20242c")
NAV_SELECTED = ("#d3e2fb", "#23314a")
NAV_SELECTED_TEXT = ("#1d4fb0", "#8fb8ff")
SECONDARY = ("#e3e7ee", "#2c313b")
SECONDARY_HOVER = ("#d4dae3", "#363c48")
DANGER = ("#c93c37", "#b8403a")
DANGER_HOVER = ("#a8302c", "#99342f")
OK = ("#1a7f37", "#3fb950")
WARN = ("#9a6700", "#d29922")
ERROR = ("#cf222e", "#f85149")
BANNER_WARN = ("#fff4dc", "#3a2f15")
BANNER_ERROR = ("#ffebe9", "#3d1f1f")

STATE_COLORS = {"ok": OK, "warn": WARN, "error": ERROR, "info": MUTED}

_fonts: dict = {}


def font(size: int = 14, weight: str = "normal", family: str | None = None) -> ctk.CTkFont:
    key = (size, weight, family)
    if key not in _fonts:
        _fonts[key] = ctk.CTkFont(family=family, size=size, weight=weight) if family else ctk.CTkFont(
            size=size, weight=weight)
    return _fonts[key]


def mono(size: int = 13) -> ctk.CTkFont:
    return font(size, family="Consolas" if sys.platform.startswith("win") else "DejaVu Sans Mono")


def is_dark() -> bool:
    return ctk.get_appearance_mode().lower() == "dark"


def pick(pair) -> str:
    """Single color for plain-tk things (text tags) that can't take a (light, dark) pair."""
    return pair[1] if is_dark() else pair[0]


def button(parent, text, command, kind="primary", **kw) -> ctk.CTkButton:
    colors = {
        "primary": dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#ffffff"),
        "secondary": dict(fg_color=SECONDARY, hover_color=SECONDARY_HOVER, text_color=TEXT),
        "danger": dict(fg_color=DANGER, hover_color=DANGER_HOVER, text_color="#ffffff"),
        "ghost": dict(fg_color="transparent", hover_color=NAV_HOVER, text_color=TEXT),
    }[kind]
    colors.update(kw)
    colors.setdefault("height", 36)
    colors.setdefault("corner_radius", 8)
    colors.setdefault("font", font(14, "bold" if kind == "primary" else "normal"))
    return ctk.CTkButton(parent, text=text, command=command, **colors)


def label(parent, text, size=14, weight="normal", color=None, wrap=0, **kw) -> ctk.CTkLabel:
    return ctk.CTkLabel(parent, text=text, font=font(size, weight), text_color=color or TEXT, anchor="w",
                        justify="left", wraplength=wrap, **kw)


def hint(parent, text, wrap=560) -> ctk.CTkLabel:
    return label(parent, text, size=12, color=MUTED, wrap=wrap)


def card(parent, title: str | None = None, subtitle: str | None = None, wrap: int = 760):
    """Rounded panel. Returns (outer, inner); put widgets in `inner` using grid (column 1 stretches)."""
    outer = ctk.CTkFrame(parent, fg_color=CARD_BG, corner_radius=14, border_width=1, border_color=CARD_BORDER)
    outer.grid_columnconfigure(0, weight=1)
    r = 0
    if title:
        label(outer, title, 16, "bold").grid(row=r, column=0, sticky="ew", padx=20, pady=(16, 0))
        r += 1
    if subtitle:
        hint(outer, subtitle, wrap=wrap).grid(row=r, column=0, sticky="ew", padx=20, pady=(2, 0))
        r += 1
    inner = ctk.CTkFrame(outer, fg_color="transparent")
    inner.grid(row=r, column=0, sticky="nsew", padx=20, pady=(12, 18))
    outer.grid_rowconfigure(r, weight=1)
    inner.grid_columnconfigure(1, weight=1)
    return outer, inner


def section(parent, title: str, subtitle: str | None = None, open_: bool = False, wrap: int = 760):
    """A card whose body folds away (for settings that are rarely changed). Click the title to open/close.

    Returns (outer, inner, set_open). Put widgets in `inner` with grid (column 1 stretches), like card().
    """
    outer = ctk.CTkFrame(parent, fg_color=CARD_BG, corner_radius=14, border_width=1, border_color=CARD_BORDER)
    outer.grid_columnconfigure(0, weight=1)
    state = {"open": open_}
    head = ctk.CTkButton(outer, text=title, anchor="w", height=40, corner_radius=10, fg_color="transparent",
                         hover_color=NAV_HOVER, text_color=TEXT, font=font(16, "bold"),
                         command=lambda: set_open(not state["open"]))
    head.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 0))
    if subtitle:
        hint(outer, subtitle, wrap=wrap).grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 0))
    spacer = ctk.CTkFrame(outer, fg_color="transparent", height=12)
    spacer.grid(row=2, column=0, sticky="ew")
    inner = ctk.CTkFrame(outer, fg_color="transparent")
    inner.grid(row=3, column=0, sticky="nsew", padx=20, pady=(4, 18))
    inner.grid_columnconfigure(1, weight=1)

    def set_open(value: bool):
        state["open"] = bool(value)
        head.configure(text=("▾   " if value else "▸   ") + title)
        if value:
            inner.grid()
        else:
            inner.grid_remove()

    set_open(open_)
    return outer, inner, set_open


def help_toggle(parent, target) -> ctk.CTkButton:
    """Small '?' button that shows or hides `target` (a help line that starts hidden)."""
    target.grid_remove()
    shown = [False]

    def flip():
        shown[0] = not shown[0]
        if shown[0]:
            target.grid()
        else:
            target.grid_remove()

    return ctk.CTkButton(parent, text="?", width=22, height=22, corner_radius=11, fg_color=SECONDARY,
                         hover_color=SECONDARY_HOVER, text_color=MUTED, font=font(12, "bold"), command=flip)


WHEEL_STEP = 90  # logical px the page moves per mouse-wheel notch
GLIDE_MS = 12  # time between glide frames
GLIDE_SHARE = 0.4  # share of the remaining distance covered each frame


class ScrollFrame(ctk.CTkScrollableFrame):
    """CTkScrollableFrame with better mouse-wheel scrolling.

    CustomTkinter's own handler moves about 20 px per wheel notch on Windows, redrawing in many tiny jumps, and
    ignores the wheel completely while the pointer is over any text box. This one moves WHEEL_STEP per notch with
    a short glide, and only lets a text box keep the wheel when it actually has more text to scroll.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._glide_target: float | None = None
        self._glide_job = None
        try:
            self._parent_canvas.configure(yscrollincrement=1)
        except Exception:  # noqa: BLE001
            pass

    # CustomTkinter binds this name to the mouse wheel for the whole app (bind_all), so overriding it is enough.
    def _mouse_wheel_all(self, event):
        try:
            self._on_wheel(event)
        except Exception:  # noqa: BLE001  (a scroll problem must never break the app)
            pass

    def _wheel_is_mine(self, widget) -> bool:
        w = widget
        while w is not None and not isinstance(w, str):
            if w is self or w is getattr(self, "_parent_canvas", None):
                return True
            if isinstance(w, (ctk.CTkScrollbar, ctk.CTkSlider)):
                return False
            if isinstance(w, ctk.CTkTextbox):
                first, last = w.yview()
                if first > 0.0 or last < 1.0:
                    return False  # the text box has its own text to scroll
            elif isinstance(w, ctk.CTkScrollableFrame):
                return False  # another scrolling area inside this one
            w = getattr(w, "master", None)
        return False

    def _on_wheel(self, event):
        if getattr(self, "_shift_pressed", False) or not self._wheel_is_mine(event.widget):
            return
        canvas = self._parent_canvas
        if tuple(canvas.yview()) == (0.0, 1.0):
            return
        if sys.platform.startswith("win"):
            notches = -event.delta / 120
        elif sys.platform == "darwin":
            notches = -event.delta / 3
        else:
            notches = -1 if getattr(event, "num", 5) == 4 else 1
        self.scroll_by(notches * WHEEL_STEP * window_scale(self))

    def _content_height(self) -> float:
        box = self._parent_canvas.bbox("all")
        return float(box[3] - box[1]) if box else 0.0

    def scroll_by(self, pixels: float) -> None:
        canvas = self._parent_canvas
        total = self._content_height()
        room = total - canvas.winfo_height()
        if total <= 0 or room <= 0:
            return
        start = self._glide_target if self._glide_target is not None else canvas.canvasy(0)
        self._glide_target = max(0.0, min(room, start + pixels))
        if self._glide_job is None:
            self._glide()

    def _glide(self):
        self._glide_job = None
        canvas = self._parent_canvas
        target, total = self._glide_target, self._content_height()
        if target is None or total <= 0:
            self._glide_target = None
            return
        cur = canvas.canvasy(0)
        left = target - cur
        if abs(left) <= 1.5:
            canvas.yview_moveto(target / total)
            self._glide_target = None
            return
        step = left * GLIDE_SHARE
        if abs(step) < 1:
            step = 1 if left > 0 else -1
        canvas.yview_moveto((cur + step) / total)
        canvas.update_idletasks()  # paint everything at the new position together (avoids torn redraws)
        self._glide_job = self.after(GLIDE_MS, self._glide)


def ask_choice(parent, title: str, message: str, options: list[tuple[str, str, str]],
               check: tuple | None = None, select: tuple | None = None) -> str | None:
    """A small window with a message and one button per option [(key, label, kind)]. Returns the key clicked,
    or None if it was closed. Blocks until answered, like a message box. check: optional (text, BooleanVar)
    shown as a tick box above the buttons. select: optional (text, StringVar, values, hint) shown as a drop-down
    row above the tick box."""
    win = ctk.CTkToplevel(parent)
    win.title(title)
    win.configure(fg_color=CONTENT_BG)
    win.resizable(False, False)
    win.transient(parent)
    answer: list = [None]
    label(win, message, 14, wrap=460).pack(padx=24, pady=(22, 16), anchor="w")
    if select:
        sf = ctk.CTkFrame(win, fg_color="transparent")
        sf.pack(padx=24, pady=(0, 12), anchor="w")
        label(sf, select[0], 14).pack(side="left")
        ctk.CTkOptionMenu(sf, values=list(select[2]), variable=select[1], width=110, height=32).pack(
            side="left", padx=10)
        if len(select) > 3 and select[3]:
            hint(sf, select[3], wrap=300).pack(side="left")
    if check:
        ctk.CTkCheckBox(win, text=check[0], variable=check[1], font=font(14)).pack(padx=24, pady=(0, 16),
                                                                                   anchor="w")
    row = ctk.CTkFrame(win, fg_color="transparent")
    row.pack(padx=24, pady=(0, 20), anchor="e")

    def pick(key):
        answer[0] = key
        win.destroy()

    for key, text, kind in options:
        button(row, text, lambda k=key: pick(k), kind=kind).pack(side="left", padx=(8, 0))
    win.protocol("WM_DELETE_WINDOW", win.destroy)
    win.after(50, lambda: (win.lift(), win.focus_force()))
    try:
        win.grab_set()
    except Exception:  # noqa: BLE001
        pass
    parent.wait_window(win)
    return answer[0]


def work_area(win) -> tuple[int, int, int, int]:
    """(left, top, width, height) in screen pixels of the usable desktop (without the taskbar)."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
                w, h = rect.right - rect.left, rect.bottom - rect.top
                if w > 0 and h > 0:
                    return rect.left, rect.top, w, h
        except Exception:  # noqa: BLE001
            pass
    try:
        return 0, 0, int(win.winfo_screenwidth()), int(win.winfo_screenheight()) - 48
    except Exception:  # noqa: BLE001
        return 0, 0, 1280, 720


def window_scale(win) -> float:
    try:
        return float(win._get_window_scaling())
    except Exception:  # noqa: BLE001
        return 1.0


def fit_window(win, width: int, height: int, min_width: int, min_height: int) -> None:
    """Size a window (logical px, like CTk's geometry) so it fits the usable screen, centered, and let it shrink
    to at least min_width x min_height (or the screen, if smaller). Matters on small laptops at 125-150%."""
    scale = window_scale(win)
    x0, y0, sw, sh = work_area(win)
    title_bar = int(32 * scale)
    w = int(min(width, sw * 0.94 / scale))
    h = int(min(height, (sh * 0.94 - title_bar) / scale))
    win.minsize(min(min_width, w), min(min_height, h))
    x = x0 + max(0, int((sw - w * scale) / 2))
    y = y0 + max(0, int((sh - h * scale - title_bar) / 2))
    win.geometry(f"{w}x{h}+{x}+{y}")


def ago(days: float | None) -> str:
    if days is None:
        return "never"
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    return f"{int(days)} days ago"


# --------------------------------------------------------------------------- opening things


# ---------------------------------------------------------------- clipboard
# On Windows the app does all copying and pasting itself, through the Windows clipboard, and Tk's own clipboard
# is never used. Tk 9's clipboard on Windows proved unreliable: inside the app it kept pasting text the app had
# copied earlier even after a link was copied in the browser, it came back empty right after copying, and it
# crashed when used from a window that wasn't shown yet. Elsewhere, Tk's clipboard is used as usual.
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _win_clipboard_api():
    import ctypes
    from ctypes import wintypes

    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u.OpenClipboard.argtypes, u.OpenClipboard.restype = [wintypes.HWND], wintypes.BOOL
    u.CloseClipboard.argtypes, u.CloseClipboard.restype = [], wintypes.BOOL
    u.EmptyClipboard.argtypes, u.EmptyClipboard.restype = [], wintypes.BOOL
    u.IsClipboardFormatAvailable.argtypes, u.IsClipboardFormatAvailable.restype = [wintypes.UINT], wintypes.BOOL
    u.GetClipboardData.argtypes, u.GetClipboardData.restype = [wintypes.UINT], wintypes.HANDLE
    u.SetClipboardData.argtypes, u.SetClipboardData.restype = [wintypes.UINT, wintypes.HANDLE], wintypes.HANDLE
    k.GlobalAlloc.argtypes, k.GlobalAlloc.restype = [wintypes.UINT, ctypes.c_size_t], wintypes.HGLOBAL
    k.GlobalLock.argtypes, k.GlobalLock.restype = [wintypes.HGLOBAL], wintypes.LPVOID
    k.GlobalUnlock.argtypes, k.GlobalUnlock.restype = [wintypes.HGLOBAL], wintypes.BOOL
    k.GlobalFree.argtypes, k.GlobalFree.restype = [wintypes.HGLOBAL], wintypes.HGLOBAL
    return ctypes, u, k


def _win_open_clipboard(u) -> bool:
    import time as _time

    for _ in range(25):  # another program may have it open for a moment
        if u.OpenClipboard(None):
            return True
        _time.sleep(0.02)
    return False


def _win_read() -> str | None:
    """The clipboard's text, "" if it holds no text, None if the clipboard couldn't be opened."""
    ctypes, u, k = _win_clipboard_api()
    if not u.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return ""
    if not _win_open_clipboard(u):
        return None
    try:
        h = u.GetClipboardData(CF_UNICODETEXT)
        ptr = k.GlobalLock(h) if h else None
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


def _win_write(text: str) -> bool:
    ctypes, u, k = _win_clipboard_api()
    data = (text + "\0").encode("utf-16-le")
    h = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        return False
    ptr = k.GlobalLock(h)
    if not ptr:
        k.GlobalFree(h)
        return False
    ctypes.memmove(ptr, data, len(data))
    k.GlobalUnlock(h)
    if not _win_open_clipboard(u):
        k.GlobalFree(h)
        return False
    try:
        u.EmptyClipboard()
        if u.SetClipboardData(CF_UNICODETEXT, h):
            return True  # Windows owns the memory now
    finally:
        u.CloseClipboard()
    k.GlobalFree(h)
    return False


def read_clipboard(widget) -> str:
    """The text on the system clipboard right now."""
    if sys.platform.startswith("win"):
        try:
            text = _win_read()
            if text is not None:
                return text
        except Exception:  # noqa: BLE001
            pass
        return ""  # never fall back to Tk's clipboard on Windows (see above)
    try:
        return widget.clipboard_get()
    except Exception:  # noqa: BLE001
        return ""


def write_clipboard(widget, text: str) -> bool:
    """Put text on the system clipboard. False if Windows wouldn't let the app (rare: another program held it)."""
    if sys.platform.startswith("win"):
        try:
            return _win_write(text)
        except Exception:  # noqa: BLE001
            return False
    widget.clipboard_clear()
    widget.clipboard_append(text)
    widget.update()
    return True


def _selected_text(w) -> str:
    try:
        if w.winfo_class() == "Text":
            return w.get("sel.first", "sel.last")
        return w.get()[w.index("sel.first"):w.index("sel.last")]
    except Exception:  # noqa: BLE001  (nothing selected)
        return ""


def install_clipboard_fix(root) -> None:
    """Copy, Cut and Paste in every text box and entry go through the Windows clipboard (Windows only)."""
    if not sys.platform.startswith("win"):
        return

    def copy(event):
        text = _selected_text(event.widget)
        if text:
            write_clipboard(root, text)
        return "break"

    def cut(event):
        w = event.widget
        text = _selected_text(w)
        if text and write_clipboard(root, text):
            try:
                if str(w.cget("state")) != "disabled":
                    w.delete("sel.first", "sel.last")
            except Exception:  # noqa: BLE001
                pass
        return "break"

    def paste(event):
        w = event.widget
        try:
            if str(w.cget("state")) in ("disabled", "readonly"):
                return "break"
        except Exception:  # noqa: BLE001
            pass
        text = read_clipboard(root)
        if not text:
            return "break"
        try:
            w.delete("sel.first", "sel.last")
        except Exception:  # noqa: BLE001  (nothing selected)
            pass
        w.insert("insert", text)
        try:
            w.see("insert")
        except Exception:  # noqa: BLE001  (entries have no see())
            pass
        return "break"

    for cls in ("Text", "Entry"):
        root.bind_class(cls, "<<Copy>>", copy)
        root.bind_class(cls, "<<Cut>>", cut)
        root.bind_class(cls, "<<Paste>>", paste)


def open_path(p: str) -> None:
    if sys.platform.startswith("win"):
        os.startfile(p)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])


def open_in_editor(p: str) -> None:
    if sys.platform.startswith("win"):
        subprocess.Popen(["notepad.exe", p])
    else:
        open_path(p)


def show_in_folder(p: str) -> None:
    if sys.platform.startswith("win") and '"' not in p:  # explorer needs this exact quoting; a path can't hold "
        subprocess.Popen(f'explorer /select,"{os.path.normpath(p)}"')
    elif sys.platform.startswith("win"):
        open_path(os.path.dirname(p))
    else:
        open_path(os.path.dirname(p))


def open_url(url: str) -> None:
    webbrowser.open(url)


# --------------------------------------------------------------------------- icon

def resource_path(rel: str) -> str:
    """Path to a bundled file, both from source and inside the PyInstaller build."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


ICON_ICO = resource_path(os.path.join("assets", "icon.ico"))
ICON_PNG = resource_path(os.path.join("assets", "icon.png"))


def set_app_id() -> None:
    """Group the window under our own taskbar icon on Windows (instead of Python's)."""
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("StreamTimestamper.App")
        except Exception:  # noqa: BLE001
            pass


def apply_window_icon(win) -> None:
    """Title-bar/taskbar icon. CustomTkinter sets its own icon ~200 ms after a window opens unless we set one."""
    if not os.path.exists(ICON_ICO):
        return

    def _set():
        try:
            if sys.platform.startswith("win"):
                win.iconbitmap(ICON_ICO)
            elif os.path.exists(ICON_PNG):
                from PIL import Image, ImageTk

                img = ImageTk.PhotoImage(Image.open(ICON_PNG).resize((64, 64)))
                win._icon_ref = img  # keep a reference
                win.iconphoto(True, img)
        except Exception:  # noqa: BLE001
            pass

    _set()
    win.after(300, _set)  # CTkToplevel resets its icon at ~200 ms


def icon_image(size: int):
    """CTkImage of the app icon, or None if Pillow/the file is missing."""
    try:
        from PIL import Image

        img = Image.open(ICON_PNG)
        return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
    except Exception:  # noqa: BLE001
        return None
