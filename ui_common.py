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
MODELS = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-4-argon"]

# (light, dark) pairs
SIDEBAR_BG = ("#e8ecf2", "#14161b")
CONTENT_BG = ("#f4f6f9", "#1a1d23")
CARD_BG = ("#ffffff", "#22262e")
CARD_BORDER = ("#dce1e9", "#2d323c")
TEXT = ("#1f2328", "#e6e8eb")
MUTED = ("#5b6472", "#8c95a3")
ACCENT = ("#2f6fe0", "#3b82f6")
ACCENT_HOVER = ("#2558b8", "#2f6fd6")
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


def ago(days: float | None) -> str:
    if days is None:
        return "never"
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    return f"{int(days)} days ago"


# --------------------------------------------------------------------------- opening things


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
