"""Stream Timestamper - main window (CustomTkinter)."""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

# A windowed (no-console) exe has no stdout/stderr; some libraries print to them.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import customtkinter as ctk  # noqa: E402

import core  # noqa: E402
import prompt_builder as pb  # noqa: E402
import runlog  # noqa: E402
import settings as store  # noqa: E402
import ui_common as ui  # noqa: E402
import updates  # noqa: E402
from version import GITHUB_REPO  # noqa: E402
from wizard import CaptionFileHelp, SetupWizard  # noqa: E402

APP_NAME = ui.APP_NAME
NO_PRESET = "(unsaved / custom)"
COOLDOWN_HOURS = 6
MIN_W, MIN_H = 900, 520  # smallest window (logical px): fits 1366x768 laptops at 125% scaling
SPLIT_TOP_MIN, SPLIT_BOTTOM_MIN = 64, 200  # Run page: smallest links area / controls + Activity area
MAX_LOG_LINES = 5000
NAV = [("run", "▶", "Run"), ("prompt", "✎", "Prompt"), ("results", "☰", "Results"), ("settings", "⚙", "Settings")]
PAGE_TEXT = {
    "run": ("Run", "Paste stream links (or add caption files) and press Start."),
    "prompt": ("Prompt", "Tell the AI what to look for and how each line should look."),
    "results": ("Results", "Read and copy what the app has saved."),
    "settings": ("Settings", "API key, where results go, which videos to process, and YouTube safety."),
}


class App(ctk.CTk):
    def __init__(self):
        loaded = store.load()
        ctk.set_appearance_mode(str(loaded.get("appearance", "System")).lower())
        ctk.set_default_color_theme("blue")
        ui.set_app_id()
        super().__init__()
        self.s = loaded
        self.title(APP_NAME)
        ui.apply_window_icon(self)
        self._fit_to_screen()
        self.configure(fg_color=ui.CONTENT_BG)

        self.q: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.wizard: SetupWizard | None = None
        self.pages: dict[str, ctk.CTkFrame] = {}
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.current_page = ""
        self._last_status_check = 0.0
        self._result_paths: list[str] = []
        self._result_buttons: list[ctk.CTkButton] = []
        self._selected_result = ""
        self._pending_select = ""
        self.caption_files: list[str] = []
        self._log_lines: list[tuple[str, str]] = []  # (line, level) of the current/last run
        self.log_window: LogWindow | None = None
        self.runlog = runlog.RunLog()
        self._split_placed = False
        self._split_before = 0
        self._split_user_moved = False

        self._init_vars()
        self._build()
        self._load_initial_prompt()
        self.show_page("run")
        self.after(100, self._poll)
        self.bind("<FocusIn>", self._on_focus, add="+")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if not self.s.get("setup_done"):
            self.after(600, self.open_wizard)
        self.after(1500, self._startup_update_check)

    # ================================================================== variables
    def _init_vars(self):
        s, sv, bv, n = self.s, tk.StringVar, tk.BooleanVar, store.fmt_num
        self.v_key = sv(value=s["api_key"])
        self.v_model = sv(value=s["model"])
        self.v_out = sv(value=s["output_dir"])
        self.v_min_stream = sv(value=n(s["min_stream_minutes"]))
        self.v_min_seg = sv(value=n(s["min_segment_minutes"]))
        self.v_lang = sv(value=s["caption_lang"])
        self.v_dmin = sv(value=n(s["delay_min"]))
        self.v_dmax = sv(value=n(s["delay_max"]))
        self.v_tab = sv(value="Streams" if s["channel_tab"] == "streams" else "Videos")
        self.v_max = sv(value=n(s["max_videos"]))
        self.v_max_run = sv(value=n(s["max_per_run"]))
        self.v_files_only = bv(value=bool(s.get("files_only", False)))
        self.v_retries = sv(value=n(s["max_retries"]))
        self.v_skip = bv(value=s["skip_existing"])
        self.v_raw = bv(value=s["save_transcript"])
        self.v_appearance = sv(value=str(s.get("appearance", "System")).capitalize())
        self.v_check_updates = bv(value=bool(s.get("check_updates", True)))
        self.v_preset = sv(value=NO_PRESET)
        self.v_is_default = bv(value=False)
        self.v_show_key = bv(value=False)
        self.v_adv = bv(value=False)
        self._loading = False
        self.form_widgets: list = []
        self.fv: dict = {}
        for k, d in pb.DEFAULT_FORM.items():
            self.fv[k] = self.v_min_seg if k == "min_minutes" else (bv(value=d) if isinstance(d, bool) else sv(value=d))
        self.v_key.trace_add("write", lambda *_: self._refresh_key_status())
        self._model_notes: list = []
        self.v_model.trace_add("write", lambda *_: self._refresh_model_notes())

    # ================================================================== window size
    def _scale(self) -> float:
        return ui.window_scale(self)

    def _fit_to_screen(self):
        ui.fit_window(self, 1140, 800, MIN_W, MIN_H)

    # ================================================================== layout
    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        builders = {"run": self._build_run, "prompt": self._build_prompt, "results": self._build_results,
                    "settings": self._build_settings}
        for key, build in builders.items():
            title, sub = PAGE_TEXT[key]
            page = ctk.CTkFrame(self, fg_color=ui.CONTENT_BG, corner_radius=0)
            page.grid_columnconfigure(0, weight=1)
            page.grid_rowconfigure(1, weight=1)
            head = ctk.CTkFrame(page, fg_color="transparent")
            head.grid(row=0, column=0, sticky="ew", padx=32, pady=(26, 0))
            ui.label(head, title, 26, "bold").pack(anchor="w")
            ui.label(head, sub, 14, color=ui.MUTED).pack(anchor="w", pady=(2, 0))
            build(page)
            self.pages[key] = page

    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, width=230, corner_radius=0, fg_color=ui.SIDEBAR_BG)
        sb.grid(row=0, column=0, sticky="nsw")
        sb.grid_propagate(False)
        sb.grid_columnconfigure(0, weight=1)

        brand = ctk.CTkFrame(sb, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=18, pady=(24, 26))
        img = ui.icon_image(44)
        if img is not None:
            ctk.CTkLabel(brand, text="", image=img, width=44, height=44).pack(side="left")
        else:
            ctk.CTkLabel(brand, text="▶", width=40, height=40, corner_radius=10, fg_color=ui.ACCENT,
                         text_color="#ffffff", font=ui.font(18, "bold")).pack(side="left")
        ui.label(brand, "Stream\nTimestamper", 16, "bold").pack(side="left", padx=12)

        for i, (key, icon, text) in enumerate(NAV):
            b = ctk.CTkButton(sb, text=f"  {icon}    {text}", anchor="w", height=46, corner_radius=10,
                              fg_color="transparent", hover_color=ui.NAV_HOVER, text_color=ui.TEXT,
                              font=ui.font(15), command=lambda k=key: self.show_page(k))
            b.grid(row=i + 1, column=0, sticky="ew", padx=12, pady=3)
            self.nav_buttons[key] = b

        sb.grid_rowconfigure(len(NAV) + 1, weight=1)
        r = len(NAV) + 2
        self.side_key = ctk.CTkButton(sb, text="", anchor="w", height=30, fg_color="transparent",
                                      hover_color=ui.NAV_HOVER, font=ui.font(13),
                                      command=lambda: self.show_page("settings"))
        self.side_key.grid(row=r, column=0, sticky="ew", padx=12)
        self.side_safe = ctk.CTkButton(sb, text="●  YouTube: no login used", anchor="w", height=30,
                                       fg_color="transparent", hover_color=ui.NAV_HOVER, font=ui.font(13),
                                       text_color=ui.OK, command=lambda: self.show_page("settings"))
        self.side_safe.grid(row=r + 1, column=0, sticky="ew", padx=12)
        ui.label(sb, f"   Version {ui.VERSION}", 11, color=ui.MUTED).grid(row=r + 2, column=0, sticky="ew",
                                                                         padx=12, pady=(8, 16))

    def show_page(self, key: str):
        for k, page in self.pages.items():
            if k != key:
                page.grid_remove()
        self.pages[key].grid(row=0, column=1, sticky="nsew")
        for k, b in self.nav_buttons.items():
            sel = k == key
            b.configure(fg_color=ui.NAV_SELECTED if sel else "transparent",
                        text_color=ui.NAV_SELECTED_TEXT if sel else ui.TEXT,
                        font=ui.font(15, "bold" if sel else "normal"))
        self.current_page = key
        if key == "results":
            self.refresh_results()
        self.refresh_status()

    # ------------------------------------------------------------------ Run page
    def _build_run(self, page):
        body = ctk.CTkFrame(page, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=32, pady=(16, 18))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

        # notice banner (hidden unless something needs attention)
        self.banner = ctk.CTkFrame(body, corner_radius=12, fg_color=ui.BANNER_WARN)
        self.banner.grid_columnconfigure(0, weight=1)
        self.banner_lbl = ui.label(self.banner, "", 14, wrap=600)
        self.banner_lbl.grid(row=0, column=0, sticky="w", padx=16, pady=12)
        self.banner_btn = ui.button(self.banner, "", lambda: None, width=170)
        self.banner_btn.grid(row=0, column=1, padx=(8, 4))
        ui.button(self.banner, "✕", self.hide_banner, kind="ghost", width=36).grid(row=0, column=2, padx=(0, 8))
        self.banner.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self.banner.grid_remove()

        # links on top, controls + Activity below, with a bar between them that can be dragged up or down
        sc = self._scale()
        self.run_split = tk.PanedWindow(body, orient="vertical", sashwidth=max(6, int(8 * sc)), sashrelief="flat",
                                        borderwidth=0, opaqueresize=True, showhandle=False,
                                        sashcursor="sb_v_double_arrow", bg=ui.pick(ui.CARD_BORDER))
        self.run_split.grid(row=1, column=0, sticky="nsew")
        top = ctk.CTkFrame(self.run_split, fg_color=ui.CONTENT_BG, corner_radius=0)
        top.grid_columnconfigure(0, weight=1)
        top.grid_rowconfigure(0, weight=1)
        links = ctk.CTkScrollableFrame(top, fg_color=ui.CONTENT_BG, corner_radius=0)
        links.grid(row=0, column=0, sticky="nsew")
        links.grid_columnconfigure(0, weight=1)
        bottom = ctk.CTkFrame(self.run_split, fg_color=ui.CONTENT_BG, corner_radius=0)
        bottom.grid_columnconfigure(0, weight=1)
        bottom.grid_rowconfigure(2, weight=1)
        self.run_split.add(top, minsize=int(SPLIT_TOP_MIN * sc), stretch="never")
        self.run_split.add(bottom, minsize=int(SPLIT_BOTTOM_MIN * sc), stretch="always")
        self.run_split.bind("<ButtonRelease-1>", self._on_split_moved, add="+")
        self.run_split.bind("<Configure>", self._on_split_configure, add="+")

        outer, inner = ui.card(links, "Stream links",
                               "One per line. Single streams, playlists, and channel links all work. "
                               "Channel links use that channel's newest streams (change how many in Settings).")
        outer.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self._links_card = outer
        inner.grid_columnconfigure(0, weight=1)
        self.urls_box = ctk.CTkTextbox(inner, height=110, font=ui.font(14), border_width=1,
                                       border_color=ui.CARD_BORDER)
        self.urls_box.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.urls_box.insert("1.0", self.s.get("last_urls", ""))
        self.links_off_lbl = ui.label(inner, "Links are turned off: 'Caption files only' is on in Settings.", 13,
                                      color=ui.WARN)
        self.links_off_lbl.grid(row=1, column=0, sticky="w", pady=(6, 0))
        ctk.CTkCheckBox(inner, text="Skip streams that already have results", variable=self.v_skip,
                        font=ui.font(13)).grid(row=2, column=0, sticky="w", pady=(10, 0))

        ff = ctk.CTkFrame(inner, fg_color="transparent")
        ff.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        ff.grid_columnconfigure(3, weight=1)
        ui.label(ff, "Or caption files:", 14, "bold").grid(row=0, column=0, sticky="w", padx=(0, 10))
        ui.button(ff, "Add caption files…", self.add_caption_files, kind="secondary", height=32).grid(
            row=0, column=1)
        ui.button(ff, "How do I get these?", self.show_caption_help, kind="ghost", height=32).grid(
            row=0, column=2, padx=6)
        self.files_lbl = ui.label(ff, "", 13, color=ui.MUTED, wrap=380)
        self.files_lbl.grid(row=0, column=3, sticky="w", padx=6)
        self.btn_clear_files = ui.button(ff, "Clear", self.clear_caption_files, kind="ghost", width=60, height=32)
        self.btn_clear_files.grid(row=0, column=4)
        ui.hint(inner, "Caption files downloaded from YouTube Studio are read straight from your PC: the app "
                       "doesn't contact YouTube at all for them.", wrap=700).grid(row=4, column=0, sticky="w",
                                                                                  pady=(4, 0))
        self._refresh_files_label()

        ctl = ctk.CTkFrame(bottom, fg_color="transparent")
        ctl.grid(row=0, column=0, sticky="ew", pady=(12, 12))
        self.btn_start = ui.button(ctl, "▶  Start", self.start, width=150, height=44, font=ui.font(16, "bold"))
        self.btn_start.pack(side="left")
        self.btn_stop = ui.button(ctl, "■  Stop", self.stop, kind="danger", width=110, height=44, state="disabled")
        self.btn_stop.pack(side="left", padx=10)
        ui.button(ctl, "Open output folder", self.open_output, kind="secondary", height=44).pack(side="right")
        self.btn_view = ui.button(ctl, "View results", lambda: self.show_page("results"), kind="secondary",
                                  height=44)
        self.btn_view.pack(side="right", padx=10)

        prog = ctk.CTkFrame(bottom, fg_color="transparent")
        prog.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        prog.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(prog, height=10, corner_radius=5, progress_color=ui.ACCENT)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.status_lbl = ui.label(prog, "Ready.", 13, color=ui.MUTED)
        self.status_lbl.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        lo, li = ui.card(bottom, "Activity")
        lo.grid(row=2, column=0, sticky="nsew")
        bar = ctk.CTkFrame(lo, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="e", padx=16, pady=(12, 0))
        ui.label(bar, "Drag the bar above to resize", 12, color=ui.MUTED).pack(side="left", padx=(0, 10))
        ui.button(bar, "Open log folder", self.open_log_folder, kind="ghost", height=28,
                  font=ui.font(13)).pack(side="left")
        ui.button(bar, "Pop out ⧉", self.open_log_window, kind="secondary", height=28,
                  font=ui.font(13)).pack(side="left", padx=(6, 0))
        li.grid_columnconfigure(0, weight=1)
        li.grid_rowconfigure(0, weight=1)
        self.log_box = ctk.CTkTextbox(li, state="disabled", font=ui.mono(12), fg_color="transparent", wrap="word")
        self.log_box.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self._config_log_tags()

    # ---- the draggable bar between the links and the Activity log
    def _split_y(self) -> int:
        try:
            return int(self.run_split.sash_coord(0)[1])
        except (tk.TclError, IndexError, TypeError, ValueError):
            return 0

    def _place_split(self, y: int):
        try:
            self.run_split.sash_place(0, 0, max(1, int(y)))
        except tk.TclError:
            pass

    def _on_split_configure(self, event=None):
        """First time the Run page has a real size: put the bar where it was last left (or fit the links)."""
        if self._split_placed:
            return
        h = self.run_split.winfo_height()
        if h < 100:
            return
        self._split_placed = True
        try:
            frac = float(self.s.get("run_split") or 0)
        except (TypeError, ValueError):
            frac = 0.0
        if 0.05 <= frac <= 0.9:
            y = int(h * frac)
        else:
            y = min(self._links_card.winfo_reqheight() + int(14 * self._scale()), int(h * 0.55))
        self._place_split(y)

    def _on_split_moved(self, event=None):
        if self.worker and self.worker.is_alive():
            self._split_user_moved = True  # keep where they put it during a run
            return
        h, y = self.run_split.winfo_height(), self._split_y()
        if h >= 100 and y > 0:
            self.s["run_split"] = round(y / h, 3)

    def _collapse_links(self):
        """During a run, give the Activity log most of the page (the links can still be scrolled or dragged open)."""
        self._split_user_moved = False
        self._split_before = self._split_y()
        self._place_split(int(SPLIT_TOP_MIN * self._scale()))

    def _restore_links(self):
        if self._split_before and not self._split_user_moved:
            self._place_split(self._split_before)
        self._split_before = 0

    # ------------------------------------------------------------------ Prompt page
    def _build_prompt(self, page):
        sc = ctk.CTkScrollableFrame(page, fg_color="transparent")
        sc.grid(row=1, column=0, sticky="nsew", padx=18, pady=(12, 16))
        sc.grid_columnconfigure(0, weight=1)
        fw = self.form_widgets

        # presets
        o, i = ui.card(sc, "Preset", "Start from a built-in preset or one you saved. Changing the boxes below "
                                     "doesn't change the preset until you save it.")
        o.grid(row=0, column=0, sticky="ew", padx=14, pady=(0, 14))
        self.preset_menu = ctk.CTkOptionMenu(i, variable=self.v_preset, values=[NO_PRESET], width=430, height=36,
                                             dynamic_resizing=False, font=ui.font(14),
                                             command=self.on_preset_chosen)
        self.preset_menu.grid(row=0, column=0, columnspan=2, sticky="w")
        pr = ctk.CTkFrame(i, fg_color="transparent")
        pr.grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ui.button(pr, "Save as my preset", self.save_preset, width=160).pack(side="left")
        ui.button(pr, "Delete", self.delete_preset, kind="secondary", width=90).pack(side="left", padx=10)
        ctk.CTkCheckBox(pr, text="Start with this preset", variable=self.v_is_default, font=ui.font(13),
                        command=self.toggle_default).pack(side="left", padx=10)

        # what to find
        o, i = ui.card(sc, "What to look for", "Describe it like you'd explain it to a person watching the stream.")
        o.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 14))
        r = 0

        def field(row, text, key, help_text=None, width=None, track=True):
            ui.label(i, text, 14).grid(row=row, column=0, sticky="nw", padx=(0, 16), pady=(8, 0))
            e = ctk.CTkEntry(i, textvariable=self.fv[key], width=width or 200, height=36, font=ui.font(14))
            e.grid(row=row, column=1, sticky="w" if width else "ew", pady=(8, 0))
            if track:
                fw.append(e)
            if help_text:
                ui.hint(i, help_text).grid(row=row + 1, column=1, sticky="w")
                return row + 2
            return row + 1

        r = field(r, "Show / channel name", "show", "Optional. Example: The Bible Guy")
        r = field(r, "About the show", "description", "Optional, one line. Example: an atheist call-in show")
        r = field(r, "What should it find?", "find",
                  "Required. Example: a caller argues against the host  /  the host interviews a guest")
        r = field(r, "What should it ignore?", "ignore", "Optional. Example: the host, ads, intros")
        # stays editable in Advanced mode ({min_minutes} still uses it)
        field(r, "Minimum length (minutes)", "min_minutes", "Shorter segments are left out. 0 = keep everything.",
              width=90, track=False)

        # each line
        o, i = ui.card(sc, "Each line includes", "The start time is always included.")
        o.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 14))
        for rr, (key, text) in enumerate([("name_on", "Name"), ("length_on", "Length (~X minutes)"),
                                          ("summary_on", "One-line summary")]):
            c = ctk.CTkCheckBox(i, text=text, variable=self.fv[key], font=ui.font(14))
            c.grid(row=rr, column=0, columnspan=2, sticky="w", pady=4)
            fw.append(c)
        dr = ctk.CTkFrame(i, fg_color="transparent")
        dr.grid(row=3, column=0, columnspan=2, sticky="w", pady=4)
        c = ctk.CTkCheckBox(dr, text="A detail column called", variable=self.fv["detail_on"], font=ui.font(14))
        c.pack(side="left")
        fw.append(c)
        e = ctk.CTkEntry(dr, textvariable=self.fv["detail_label"], width=130, height=34)
        e.pack(side="left", padx=8)
        fw.append(e)
        ui.label(dr, "with examples:", 13, color=ui.MUTED).pack(side="left", padx=(6, 6))
        e = ctk.CTkEntry(dr, textvariable=self.fv["detail_examples"], width=320, height=34)
        e.pack(side="left")
        fw.append(e)

        # layout
        o, i = ui.card(sc, "Layout")
        o.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 14))
        lr = ctk.CTkFrame(i, fg_color="transparent")
        lr.grid(row=0, column=0, columnspan=2, sticky="w")
        ui.label(lr, "Separator", 14).pack(side="left")
        m = ctk.CTkOptionMenu(lr, values=pb.SEPARATORS, variable=self.fv["separator"], width=70, height=34)
        m.pack(side="left", padx=(8, 24))
        fw.append(m)
        ui.label(lr, "Title line (blank = none)", 14).pack(side="left")
        e = ctk.CTkEntry(lr, textvariable=self.fv["header"], width=170, height=34)
        e.pack(side="left", padx=8)
        fw.append(e)
        for rr, (key, text) in enumerate([("blank_between", "Blank line between entries"),
                                          ("trailing_period", "End the file with a single period (.)")], start=1):
            c = ctk.CTkCheckBox(i, text=text, variable=self.fv[key], font=ui.font(14))
            c.grid(row=rr, column=0, columnspan=2, sticky="w", pady=(8 if rr == 1 else 4, 0))
            fw.append(c)
        chf = ctk.CTkFrame(i, fg_color="transparent")
        chf.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        c = ctk.CTkCheckBox(chf, text="Make it work as YouTube chapters: start with a 00:00 line called",
                            variable=self.fv["chapters"], font=ui.font(14))
        c.pack(side="left")
        fw.append(c)
        e = ctk.CTkEntry(chf, textvariable=self.fv["chapter_intro"], width=120, height=34)
        e.pack(side="left", padx=8)
        fw.append(e)
        ui.hint(i, "Timestamps in a description or comment are clickable either way. Chapters (the sections on "
                   "the video's progress bar) also need a 00:00 first line and at least 3 timestamps, each 10+ "
                   "seconds apart.", wrap=700).grid(row=4, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # preview
        o, i = ui.card(sc, "Instructions sent to the AI",
                       "Built from the boxes above. Tick Advanced to edit the text by hand "
                       "({min_minutes} is replaced by the minimum length).")
        o.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 8))
        i.grid_columnconfigure(0, weight=1)
        ctk.CTkCheckBox(i, text="Advanced: edit this text by hand", variable=self.v_adv, font=ui.font(13),
                        command=self.on_adv_toggle).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.preview_box = ctk.CTkTextbox(i, height=300, wrap="word", font=ui.mono(12), border_width=1,
                                          border_color=ui.CARD_BORDER)
        self.preview_box.grid(row=1, column=0, columnspan=2, sticky="ew")

        for var in self.fv.values():
            var.trace_add("write", lambda *_: self._on_form_change())

    # ------------------------------------------------------------------ Results page
    def _build_results(self, page):
        body = ctk.CTkFrame(page, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=32, pady=(16, 24))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        lo = ctk.CTkFrame(body, width=340, fg_color=ui.CARD_BG, corner_radius=14, border_width=1,
                          border_color=ui.CARD_BORDER)
        lo.grid(row=0, column=0, sticky="nsw", padx=(0, 16))
        lo.grid_propagate(False)
        lo.grid_columnconfigure(0, weight=1)
        lo.grid_rowconfigure(1, weight=1)
        lh = ctk.CTkFrame(lo, fg_color="transparent")
        lh.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        self.results_count = ui.label(lh, "Saved results", 15, "bold")
        self.results_count.pack(side="left")
        ui.button(lh, "↻ Refresh", self.refresh_results, kind="ghost", width=90, height=30).pack(side="right")
        self.results_list = ctk.CTkScrollableFrame(lo, fg_color="transparent")
        self.results_list.grid(row=1, column=0, sticky="nsew", padx=6)
        self.results_list.grid_columnconfigure(0, weight=1)
        ui.button(lo, "Open output folder", self.open_output, kind="secondary").grid(
            row=2, column=0, sticky="ew", padx=16, pady=14)

        vo = ctk.CTkFrame(body, fg_color=ui.CARD_BG, corner_radius=14, border_width=1, border_color=ui.CARD_BORDER)
        vo.grid(row=0, column=1, sticky="nsew")
        vo.grid_columnconfigure(0, weight=1)
        vo.grid_rowconfigure(2, weight=1)
        self.view_title = ui.label(vo, "Pick a result on the left", 16, "bold", wrap=560)
        self.view_title.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        vb = ctk.CTkFrame(vo, fg_color="transparent")
        vb.grid(row=1, column=0, sticky="ew", padx=20)
        self.btn_copy = ui.button(vb, "Copy all", self.copy_result, width=120, state="disabled")
        self.btn_copy.pack(side="left")
        self.btn_open_res = ui.button(vb, "Open in Notepad", self.open_result, kind="secondary", state="disabled")
        self.btn_open_res.pack(side="left", padx=10)
        self.btn_show_res = ui.button(vb, "Show in folder", self.show_result, kind="secondary", state="disabled")
        self.btn_show_res.pack(side="left")
        self.view_box = ctk.CTkTextbox(vo, wrap="word", font=ui.mono(13), state="disabled", border_width=1,
                                       border_color=ui.CARD_BORDER)
        self.view_box.grid(row=2, column=0, sticky="nsew", padx=20, pady=(12, 20))

    # ------------------------------------------------------------------ Settings page
    def _build_settings(self, page):
        sc = ctk.CTkScrollableFrame(page, fg_color="transparent")
        sc.grid(row=1, column=0, sticky="nsew", padx=18, pady=(12, 16))
        sc.grid_columnconfigure(0, weight=1)
        row = [0]

        def add_card(title, sub=None):
            o, i = ui.card(sc, title, sub)
            o.grid(row=row[0], column=0, sticky="ew", padx=14, pady=(0, 14))
            row[0] += 1
            return i

        def srow(inner, r, text, widget, help_text=None, sticky="w"):
            ui.label(inner, text, 14).grid(row=r, column=0, sticky="nw", padx=(0, 20), pady=(10, 0))
            widget.grid(row=r, column=1, sticky=sticky, pady=(8, 0))
            if help_text:
                ui.hint(inner, help_text).grid(row=r + 1, column=1, sticky="w")
                return r + 2
            return r + 1

        def num_entry(inner, var):
            return ctk.CTkEntry(inner, textvariable=var, width=90, height=34, font=ui.font(14))

        # Gemini
        i = add_card("Gemini AI", "Reads the captions and finds your segments.")
        kf = ctk.CTkFrame(i, fg_color="transparent")
        kf.grid_columnconfigure(0, weight=1)
        self.key_entry = ctk.CTkEntry(kf, textvariable=self.v_key, show="•", height=36, font=ui.font(14))
        self.key_entry.grid(row=0, column=0, sticky="ew")
        ctk.CTkCheckBox(kf, text="Show", variable=self.v_show_key, width=70, command=self.toggle_key).grid(
            row=0, column=1, padx=10)
        ui.button(kf, "Test", self.test_key, kind="secondary", width=70).grid(row=0, column=2)
        r = srow(i, 0, "API key", kf, "Get one at aistudio.google.com/apikey, or use Settings → Run setup again.",
                 sticky="ew")
        r = srow(i, r, "AI model", ctk.CTkComboBox(i, values=ui.MODELS, variable=self.v_model, width=260,
                                                    height=34))
        self.add_model_note(i).grid(row=r, column=1, sticky="w", pady=(4, 0))
        ui.hint(i, "You can also type any Gemini model name.").grid(row=r + 1, column=1, sticky="w")

        # Output
        i = add_card("Where results go")
        of = ctk.CTkFrame(i, fg_color="transparent")
        of.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(of, textvariable=self.v_out, height=36, font=ui.font(14)).grid(row=0, column=0, sticky="ew")
        ui.button(of, "Browse...", self.browse_out, kind="secondary", width=100).grid(row=0, column=1, padx=(10, 0))
        ui.button(of, "Open", self.open_output, kind="secondary", width=70).grid(row=0, column=2, padx=(10, 0))
        r = srow(i, 0, "Output folder", of, sticky="ew")
        ctk.CTkCheckBox(i, text="Also save the raw captions (.transcript.txt), handy for checking the AI's work",
                        variable=self.v_raw, font=ui.font(13)).grid(row=r, column=1, sticky="w", pady=(12, 0))

        # Which videos
        i = add_card("Which videos")
        r = srow(i, 0, "Skip streams shorter than", self._with_unit(i, self.v_min_stream, "minutes"),
                 "Streams shorter than this are ignored.")
        r = srow(i, r, "Channel links use", ctk.CTkOptionMenu(i, values=["Streams", "Videos"], variable=self.v_tab,
                                                               width=130, height=34),
                 "Which tab of a channel to take videos from.")
        r = srow(i, r, "From channels / playlists, take", self._with_unit(i, self.v_max, "newest videos (0 = all)"))
        srow(i, r, "Caption language", ctk.CTkEntry(i, textvariable=self.v_lang, width=90, height=34),
             "Language code of the captions to read, e.g. en, es, de.")

        # YouTube safety
        i = add_card("YouTube safety", "The app never signs in to YouTube: no login, cookies, or browser data are "
                                       "used. These limits keep its logged-out requests slow and polite.")
        wf = ctk.CTkFrame(i, fg_color="transparent")
        num_entry(wf, self.v_dmin).pack(side="left")
        ui.label(wf, "  to  ", 14).pack(side="left")
        num_entry(wf, self.v_dmax).pack(side="left")
        ui.label(wf, "  seconds", 14).pack(side="left")
        r = srow(i, 0, "Wait between videos", wf, "A random pause between videos. Lower = faster, but more likely "
                                                  "to get limited.")
        r = srow(i, r, "Videos per run", self._with_unit(i, self.v_max_run, "at most (0 = no limit)"),
                 "Big batches are what get noticed. Spread them out over the day.")
        ctk.CTkCheckBox(i, text="Caption files only: the app never contacts YouTube (links are turned off)",
                        variable=self.v_files_only, font=ui.font(13), command=self._refresh_files_only).grid(
            row=r, column=1, sticky="w", pady=(12, 0))
        r += 1
        ui.button(i, "How to get caption files from YouTube Studio", self.show_caption_help, kind="secondary").grid(
            row=r, column=1, sticky="w", pady=(10, 0))
        r += 1
        ui.hint(i, "If YouTube ever says 'too many requests' or asks you to confirm you're not a bot, the app stops "
                   "on its own. Wait several hours before trying again, or use caption files.", wrap=600).grid(
            row=r, column=1, sticky="w", pady=(10, 0))

        # AI retries
        i = add_card("AI")
        srow(i, 0, "Retries when Gemini is busy", num_entry(i, self.v_retries))

        # App
        i = add_card("App")
        r = srow(i, 0, "Appearance", ctk.CTkOptionMenu(i, values=["System", "Light", "Dark"],
                                                        variable=self.v_appearance, width=130, height=34,
                                                        command=self.change_appearance))
        af = ctk.CTkFrame(i, fg_color="transparent")
        ui.button(af, "Run setup again", lambda: self.open_wizard(), kind="secondary").pack(side="left")
        ui.button(af, "Open settings folder", lambda: ui.open_path(str(store.app_dir())),
                  kind="secondary").pack(side="left", padx=10)
        r = srow(i, r, "Setup", af)
        r = srow(i, r, "Version", ui.label(i, ui.VERSION, 14, color=ui.MUTED))
        if updates.repo_ok(GITHUB_REPO):
            uf = ctk.CTkFrame(i, fg_color="transparent")
            ctk.CTkCheckBox(uf, text="Tell me when a new version is out", variable=self.v_check_updates,
                            font=ui.font(13)).pack(side="left")
            ui.button(uf, "Check now", lambda: self.check_updates(manual=True), kind="secondary",
                      width=110).pack(side="left", padx=12)
            r = srow(i, r, "Updates", uf, "Checks GitHub once a day for a newer version and shows a link. It never "
                                           "downloads or installs anything by itself.")
            ui.button(i, "Help & report a problem", lambda: ui.open_url(updates.help_page(GITHUB_REPO)),
                      kind="secondary").grid(row=r, column=1, sticky="w", pady=(10, 0))
        else:
            srow(i, r, "Updates", ui.hint(i, "Update notices are only in copies downloaded from the project's "
                                             "GitHub Releases page."))

    def _with_unit(self, parent, var, unit):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkEntry(f, textvariable=var, width=90, height=34, font=ui.font(14)).pack(side="left")
        ui.label(f, "  " + unit, 14).pack(side="left")
        return f

    # ---- the one-line note under each AI model box (Settings and the setup wizard)
    def add_model_note(self, parent):
        lbl = ui.label(parent, ui.model_note(self.v_model.get()), 12, color=ui.MUTED, wrap=520)
        self._model_notes.append(lbl)
        return lbl

    def _refresh_model_notes(self):
        text = ui.model_note(self.v_model.get())
        alive = []
        for lbl in self._model_notes:
            try:
                if lbl.winfo_exists():
                    lbl.configure(text=text)
                    alive.append(lbl)
            except Exception:  # noqa: BLE001  (label from a closed wizard)
                pass
        self._model_notes = alive

    # ================================================================== status
    def _on_focus(self, _event=None):
        if time.time() - self._last_status_check > 3:
            self.refresh_status()

    def _refresh_key_status(self):
        has = bool(self.v_key.get().strip())
        self.side_key.configure(text="●  Gemini key: set" if has else "●  Gemini key: missing",
                                text_color=ui.OK if has else ui.ERROR)

    def refresh_status(self):
        self._last_status_check = time.time()
        self._refresh_key_status()
        self._refresh_files_only()

    def _refresh_files_only(self):
        on = bool(self.v_files_only.get())
        self.urls_box.configure(state="disabled" if on else "normal")
        if on:
            self.links_off_lbl.grid()
        else:
            self.links_off_lbl.grid_remove()
        self.side_safe.configure(text="●  YouTube: never contacted" if on else "●  YouTube: no login used")

    def show_banner(self, text: str, kind: str = "warn", button: str | None = None, command=None,
                    is_update: bool = False):
        self._banner_visible, self._banner_is_update = True, is_update
        self.banner.configure(fg_color={"error": ui.BANNER_ERROR, "warn": ui.BANNER_WARN}.get(kind, ui.CARD_BG))
        self.banner_lbl.configure(text=text)
        if button and command:
            self.banner_btn.configure(text=button, command=command)
            self.banner_btn.grid()
        else:
            self.banner_btn.grid_remove()
        self.banner.grid()

    def hide_banner(self):
        self._banner_visible = False
        self.banner.grid_remove()

    # ================================================================== caption files
    def show_caption_help(self):
        CaptionFileHelp(self, on_add=self.add_caption_files)

    def add_caption_files(self):
        exts = " ".join("*" + e for e in core.CAPTION_FILE_EXTS)
        files = filedialog.askopenfilenames(
            title="Choose caption files (.vtt, .srt, .sbv)",
            filetypes=[("Caption files", exts), ("All files", "*.*")])
        for f in files or ():
            if f not in self.caption_files:
                self.caption_files.append(f)
        self._refresh_files_label()

    def clear_caption_files(self):
        self.caption_files = []
        self._refresh_files_label()

    def _refresh_files_label(self):
        n = len(self.caption_files)
        if not n:
            self.files_lbl.configure(text="none added")
            self.btn_clear_files.grid_remove()
            return
        names = ", ".join(os.path.basename(f) for f in self.caption_files[:3])
        more = f" and {n - 3} more" if n > 3 else ""
        self.files_lbl.configure(text=f"{n} file{'s' if n != 1 else ''}: {names}{more}")
        self.btn_clear_files.grid()

    # ================================================================== wizard
    def open_wizard(self, only: str | None = None):
        if self.wizard is not None and self.wizard.winfo_exists():
            self.wizard.lift()
            return
        self.collect()
        self.wizard = SetupWizard(self, only=only)

    def on_wizard_closed(self):
        self.wizard = None
        self.refresh_status()

    # ================================================================== settings <-> widgets
    def collect(self) -> dict | None:
        """Copy widget values into self.s. Returns None (after showing a message) if a number is invalid."""

        def num(var, name, kind=float, lo=0):
            try:
                v = kind(float(var.get().strip()))
            except ValueError:
                raise ValueError(f"'{name}' must be a number.")
            if v < lo:
                raise ValueError(f"'{name}' can't be negative.")
            return v

        try:
            s = self.s
            s["max_per_run"] = num(self.v_max_run, "Videos per run", int)
            s["min_stream_minutes"] = num(self.v_min_stream, "Skip streams shorter than")
            s["min_segment_minutes"] = num(self.v_min_seg, "Minimum length")
            s["delay_min"] = num(self.v_dmin, "Wait between videos (first box)")
            s["delay_max"] = num(self.v_dmax, "Wait between videos (second box)")
            s["max_videos"] = num(self.v_max, "Newest videos", int)
            s["max_retries"] = max(1, num(self.v_retries, "Retries", int))
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e))
            return None
        s["api_key"] = self.v_key.get().strip()
        s["model"] = self.v_model.get().strip()
        s["output_dir"] = self.v_out.get().strip()
        s["caption_lang"] = self.v_lang.get().strip() or "en"
        s["channel_tab"] = "streams" if self.v_tab.get() == "Streams" else "videos"
        s["skip_existing"] = bool(self.v_skip.get())
        s["save_transcript"] = bool(self.v_raw.get())
        s["appearance"] = self.v_appearance.get()
        s["files_only"] = bool(self.v_files_only.get())
        s["check_updates"] = bool(self.v_check_updates.get())
        adv = bool(self.v_adv.get())
        s["prompt_mode"] = "advanced" if adv else "guided"
        s["form"] = self.get_form()
        if adv:
            s["advanced_text"] = self._preview_text()
        s["draft_prompt"] = self._preview_text() if adv else pb.build_prompt(s["form"]).strip()
        s["last_urls"] = self.urls_box.get("1.0", "end").strip()
        s["selected_prompt"] = "" if self.v_preset.get() == NO_PRESET else self.v_preset.get()
        return s

    def persist(self):
        if self.collect() is not None:
            try:
                store.save(self.s)
            except OSError as e:
                messagebox.showwarning(APP_NAME, f"Could not save settings: {e}")

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_NAME, "A run is in progress. Stop it and quit?"):
                return
            self.stop_event.set()
        self.runlog.close()
        try:
            self.collect()
            store.save(self.s)
        except Exception:  # noqa: BLE001
            pass
        self.destroy()

    def change_appearance(self, value: str):
        self.v_appearance.set(value)
        ctk.set_appearance_mode(value.lower())
        self._config_log_tags()
        try:
            self.run_split.configure(bg=ui.pick(ui.CARD_BORDER))
        except tk.TclError:
            pass
        if self.log_window is not None and self.log_window.winfo_exists():
            self.log_window.config_tags()
        self.persist()

    # ================================================================== prompt form + presets
    def get_form(self) -> dict:
        return {k: v.get() for k, v in self.fv.items()}

    def set_form(self, form: dict):
        form = pb.normalize_form(form)
        self._loading = True
        try:
            for k, var in self.fv.items():
                var.set(form[k] if isinstance(form[k], bool) else str(form[k]))
        finally:
            self._loading = False

    def _set_form_state(self, state: str):
        for w in self.form_widgets:
            try:
                w.configure(state=state)
            except Exception:  # noqa: BLE001
                pass

    def _set_preview(self, text: str):
        self.preview_box.configure(state="normal")
        self.preview_box.delete("1.0", "end")
        self.preview_box.insert("1.0", text)
        self.preview_box.configure(state="normal" if self.v_adv.get() else "disabled")

    def _preview_text(self) -> str:
        return self.preview_box.get("1.0", "end").strip()

    def _refresh_preview(self):
        self._set_preview(pb.build_prompt(self.get_form()))

    def _on_form_change(self):
        if not self._loading and not self.v_adv.get():
            self._refresh_preview()

    def on_adv_toggle(self):
        if self.v_adv.get():
            self._set_form_state("disabled")
            self.preview_box.configure(state="normal")
            return
        generated = pb.build_prompt(self.get_form()).strip()
        if self._preview_text() != generated:
            if not messagebox.askyesno(APP_NAME, "Go back to the form? Your hand edits to the text will be lost."):
                self.v_adv.set(True)
                return
        self._set_form_state("normal")
        self._refresh_preview()

    def _apply_state(self, form: dict, advanced: bool, text: str = ""):
        self.set_form(form)
        self.v_adv.set(advanced)
        if advanced:
            self._set_form_state("disabled")
            self._set_preview(text or pb.build_prompt(self.get_form()))
        else:
            self._set_form_state("normal")
            self._refresh_preview()

    def _labels(self) -> list[str]:
        return [pb.BUILTIN_PREFIX + n for n in pb.BUILTIN_PRESETS] + sorted(self.s["prompts"])

    def _refresh_presets(self, selected: str = ""):
        labels = self._labels()
        self.preset_menu.configure(values=[NO_PRESET] + labels)
        self.v_preset.set(selected if selected in labels else NO_PRESET)
        self.v_is_default.set(bool(selected) and selected == self.s.get("default_prompt", ""))

    def _apply_preset(self, label: str):
        if label.startswith(pb.BUILTIN_PREFIX):
            form = pb.builtin_form(label[len(pb.BUILTIN_PREFIX):])
            for keep in ("show", "description"):  # don't make the user retype their show name
                if self.fv[keep].get().strip():
                    form[keep] = self.fv[keep].get()
            self._apply_state(form, False)
            return
        p = self.s["prompts"].get(label)
        if isinstance(p, str):  # preset saved by an older version
            self._apply_state({}, True, p)
        elif isinstance(p, dict):
            self._apply_state(p.get("form", {}), p.get("mode") == "advanced", p.get("text", ""))

    def _load_initial_prompt(self):
        s, labels = self.s, self._labels()
        default = s.get("default_prompt", "")
        if default in labels:
            self._apply_preset(default)
            self._refresh_presets(default)
        elif s.get("form") or s.get("advanced_text") or s.get("draft_prompt"):
            text = s.get("advanced_text") or (s.get("draft_prompt") if not s.get("form") else "")
            adv = s.get("prompt_mode") == "advanced" or (not s.get("form") and bool(text))
            self._apply_state(s.get("form", {}), adv, text)
            sel = s.get("selected_prompt", "")
            self._refresh_presets(sel if sel in labels else "")
        else:
            first = pb.BUILTIN_PREFIX + next(iter(pb.BUILTIN_PRESETS))
            self._apply_preset(first)
            self._refresh_presets(first)

    def on_preset_chosen(self, label: str):
        if label == NO_PRESET:
            self.v_is_default.set(False)
            return
        self._apply_preset(label)
        self.v_is_default.set(label == self.s.get("default_prompt", ""))

    def save_preset(self):
        current = self.v_preset.get()
        dlg = ctk.CTkInputDialog(text="Name for this preset (use an existing name to overwrite it):",
                                 title="Save preset")
        name = (dlg.get_input() or "").strip()
        if not name:
            return
        if name.startswith(pb.BUILTIN_PREFIX):
            messagebox.showinfo(APP_NAME, "Pick a different name - that one is reserved for built-in presets.")
            return
        if name in self.s["prompts"] and name != current:
            if not messagebox.askyesno(APP_NAME, f"Replace your existing preset '{name}'?"):
                return
        adv = bool(self.v_adv.get())
        self.s["prompts"][name] = {"mode": "advanced" if adv else "guided", "form": self.get_form(),
                                   "text": self._preview_text() if adv else ""}
        self._refresh_presets(name)
        self.persist()

    def delete_preset(self):
        name = self.v_preset.get()
        if name.startswith(pb.BUILTIN_PREFIX):
            messagebox.showinfo(APP_NAME, "Built-in presets can't be deleted. "
                                          "(You can edit the boxes and save your own copy.)")
            return
        if name not in self.s["prompts"]:
            messagebox.showinfo(APP_NAME, "Pick one of your saved presets first.")
            return
        if not messagebox.askyesno(APP_NAME, f"Delete your preset '{name}'?"):
            return
        del self.s["prompts"][name]
        if self.s.get("default_prompt") == name:
            self.s["default_prompt"] = ""
        self._refresh_presets("")
        self.persist()

    def toggle_default(self):
        name = self.v_preset.get()
        if name == NO_PRESET:
            self.v_is_default.set(False)
            messagebox.showinfo(APP_NAME, "Pick a preset (or save your settings as one) first.")
            return
        self.s["default_prompt"] = name if self.v_is_default.get() else ""
        self.persist()

    # ================================================================== settings actions
    def toggle_key(self):
        self.key_entry.configure(show="" if self.v_show_key.get() else "•")

    def browse_out(self):
        d = filedialog.askdirectory(title="Choose output folder", initialdir=self.v_out.get() or None)
        if d:
            self.v_out.set(d)
            self.persist()

    def test_key(self):
        key, model = self.v_key.get().strip(), self.v_model.get().strip()
        if not key:
            messagebox.showinfo(APP_NAME, "Enter an API key first.")
            return
        self.status_lbl.configure(text="Testing API key ...")

        def work():
            ok, msg = core.test_api_key(key, model)
            self.q.put(("keytest", ok, msg))

        threading.Thread(target=work, daemon=True).start()

    def open_output(self):
        d = self.v_out.get().strip()
        if not d:
            return
        try:
            os.makedirs(d, exist_ok=True)
            ui.open_path(d)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Could not open the folder: {e}")

    # ================================================================== results
    def refresh_results(self):
        for b in self._result_buttons:
            b.destroy()
        self._result_buttons, self._result_paths = [], []
        items = core.list_results(self.v_out.get().strip())
        self.results_count.configure(text=f"Saved results ({len(items)})" if items else "Saved results")
        if not items:
            lb = ui.label(self.results_list, "Nothing here yet. Run a stream and its result shows up here.", 13,
                          color=ui.MUTED, wrap=280)
            lb.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
            self._result_buttons.append(lb)
        for idx, (mtime, path) in enumerate(items):
            title = core.result_title(path)
            short = title if len(title) <= 38 else title[:36] + "…"
            when = time.strftime("%b %d, %Y  %I:%M %p", time.localtime(mtime))
            b = ctk.CTkButton(self.results_list, text=f"{short}\n{when}", anchor="w", height=54, corner_radius=8,
                              fg_color="transparent", hover_color=ui.NAV_HOVER, text_color=ui.TEXT,
                              font=ui.font(13), command=lambda p=path: self.select_result(p))
            b.grid(row=idx, column=0, sticky="ew", pady=2)
            self._result_buttons.append(b)
            self._result_paths.append(path)
        want = self._pending_select or self._selected_result
        self._pending_select = ""
        if want in self._result_paths:
            self.select_result(want)
        elif self._result_paths:
            self.select_result(self._result_paths[0])
        else:
            self._show_result_text("", "Pick a result on the left")

    def select_result(self, path: str):
        self._selected_result = path
        for p, b in zip(self._result_paths, self._result_buttons[-len(self._result_paths):]):
            b.configure(fg_color=ui.NAV_SELECTED if p == path else "transparent")
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read(2_000_000)  # a stray huge .txt in the folder must not freeze the viewer
        except OSError as e:
            text = f"(Couldn't open this file: {e})"
        self._show_result_text(text, core.result_title(path))

    def _show_result_text(self, text: str, title: str):
        self.view_title.configure(text=title)
        self.view_box.configure(state="normal")
        self.view_box.delete("1.0", "end")
        self.view_box.insert("1.0", text)
        self.view_box.configure(state="disabled")
        state = "normal" if text else "disabled"
        for b in (self.btn_copy, self.btn_open_res, self.btn_show_res):
            b.configure(state=state)

    def copy_result(self):
        text = self.view_box.get("1.0", "end").rstrip("\n")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.btn_copy.configure(text="Copied ✓")
        self.after(1500, lambda: self.btn_copy.configure(text="Copy all"))

    def open_result(self):
        if self._selected_result:
            ui.open_in_editor(self._selected_result)

    def show_result(self):
        if self._selected_result:
            ui.show_in_folder(self._selected_result)

    # ================================================================== run control
    def start(self):
        if self.worker and self.worker.is_alive():
            return
        if self.collect() is None:
            return
        if not self.s["api_key"]:
            if messagebox.askyesno(APP_NAME, "You haven't added your Gemini API key yet. Set it up now?"):
                self.open_wizard("gemini")
            return
        self.refresh_status()
        if self.s["prompt_mode"] == "guided":
            errs = pb.validate_form(self.s["form"])
            if errs:
                self.show_page("prompt")
                messagebox.showinfo(APP_NAME, "\n".join(errs))
                return
        files_only = bool(self.s.get("files_only"))
        urls = "" if files_only else self.urls_box.get("1.0", "end")
        files = [f for f in self.caption_files if os.path.isfile(f)]
        if not urls.strip() and not files:
            messagebox.showinfo(APP_NAME, "Add a caption file first." if files_only else
                                "Paste at least one YouTube link or add a caption file first.")
            return
        if urls.strip():
            since = time.time() - float(self.s.get("last_blocked") or 0)
            if since < COOLDOWN_HOURS * 3600:
                mins = int(since // 60)
                ago = f"{mins} minutes" if mins < 120 else f"{mins // 60} hours"
                if not messagebox.askyesno(
                        APP_NAME, f"YouTube limited this internet connection {ago} ago. Giving it a few more hours "
                                  f"is safer.\n\nCaption files still work any time.\n\nTry YouTube links anyway?"):
                    return
        self.persist()
        try:
            cfg = core.RunConfig.from_settings(self.s)
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e))
            return

        self.stop_event.clear()
        self._clear_log()
        self.runlog.start(self._log_header(cfg, urls, files))
        self.progress.set(0)
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self._collapse_links()
        self.btn_view.configure(fg_color=ui.SECONDARY, hover_color=ui.SECONDARY_HOVER, text_color=ui.TEXT,
                                text="View results")
        self.hide_banner()
        runner = core.Runner(
            cfg, urls,
            log=lambda m, lvl="info": self.q.put(("log", m, lvl)),
            progress=lambda d, t: self.q.put(("progress", d, t)),
            status=lambda m: self.q.put(("status", m)),
            stop=self.stop_event,
            files=files,
        )

        def work():
            summary = runner.run()
            self.q.put(("finished", summary))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        self.btn_stop.configure(state="disabled")
        self.status_lbl.configure(text="Stopping after the current step ...")

    # ================================================================== queue -> UI
    def _config_log_tags(self):
        config_log_tags(self.log_box)

    def _clear_log(self):
        self._log_lines = []
        for box in self._log_boxes():
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.configure(state="disabled")

    def _log_boxes(self) -> list:
        boxes = [self.log_box]
        if self.log_window is not None and self.log_window.winfo_exists():
            boxes.append(self.log_window.box)
        return boxes

    def _append_log(self, msg: str, level: str):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self._log_lines.append((line, level))
        if len(self._log_lines) > MAX_LOG_LINES:
            del self._log_lines[:len(self._log_lines) - MAX_LOG_LINES]
        for box in self._log_boxes():
            append_log_line(box, line, level)
        self.runlog.write(core.redact(line, self.s.get("api_key", "")))

    def _log_header(self, cfg, urls: str, files: list) -> list[str]:
        import platform
        n_links = len([u for u in urls.splitlines() if u.strip()])
        return [f"{APP_NAME} {ui.VERSION} - run started {time.strftime('%Y-%m-%d %H:%M:%S')}",
                f"Windows: {platform.platform()}",
                f"Model: {cfg.model} | links: {n_links} | caption files: {len(files)}",
                ""]

    def open_log_window(self):
        if self.log_window is not None and self.log_window.winfo_exists():
            self.log_window.deiconify()
            self.log_window.lift()
            self.log_window.focus_force()
            return
        self.log_window = LogWindow(self)

    def open_log_folder(self):
        try:
            ui.open_path(str(runlog.log_dir()))
        except OSError as e:
            messagebox.showwarning(APP_NAME, f"Couldn't open the log folder: {e}")

    # ================================================================== update notice
    def _startup_update_check(self):
        if not updates.repo_ok(GITHUB_REPO) or not self.s.get("check_updates", True):
            return
        newer = updates.parse_version(self.s.get("update_available"))
        cur = updates.parse_version(ui.VERSION)
        if newer and cur and newer > cur:
            self._show_update_banner(updates.fmt_version(newer))  # remembered from an earlier check
        elif self.s.get("update_available"):
            self.s["update_available"] = ""  # already updated
        if updates.is_due(self.s.get("last_update_check")):
            self.check_updates(manual=False)

    def check_updates(self, manual: bool = False):
        if not updates.repo_ok(GITHUB_REPO):
            return
        if manual:
            self.status_lbl.configure(text="Checking for a new version ...")

        def work():
            status, version = updates.check(GITHUB_REPO, ui.VERSION)
            self.q.put(("update", status, version, manual))

        threading.Thread(target=work, daemon=True).start()

    def _on_update_result(self, status: str, version, manual: bool):
        if status != "failed":
            self.s["last_update_check"] = time.time()
        if status == "newer" and version:
            self.s["update_available"] = version
            self._show_update_banner(version)
        elif status == "current":
            self.s["update_available"] = ""
        if manual:
            self.status_lbl.configure(text="Ready.")
            if status == "newer":
                if messagebox.askyesno(APP_NAME, f"Version {version} is available (you have {ui.VERSION}).\n\n"
                                                 "Open the download page?"):
                    ui.open_url(updates.releases_page(GITHUB_REPO))
            elif status == "current":
                messagebox.showinfo(APP_NAME, f"You have the newest version ({ui.VERSION}).")
            else:
                messagebox.showwarning(APP_NAME, "Couldn't reach GitHub to check. Try again later.")

    def _show_update_banner(self, version: str):
        if getattr(self, "_banner_visible", False) and not getattr(self, "_banner_is_update", False):
            return  # don't cover a more important message (e.g. a YouTube block warning)
        self.show_banner(f"Stream Timestamper {version} is available (you have {ui.VERSION}). Download it from the "
                         "project's Releases page, then replace your app folder. Your settings are kept.", "info",
                         "Open download page", lambda: ui.open_url(updates.releases_page(GITHUB_REPO)),
                         is_update=True)

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._append_log(item[1], item[2])
                elif kind == "progress":
                    done, total = item[1], item[2]
                    self.progress.set(done / total if total else 0)
                elif kind == "status":
                    self.status_lbl.configure(text=item[1])
                elif kind == "keytest":
                    self.status_lbl.configure(text="Ready.")
                    (messagebox.showinfo if item[1] else messagebox.showerror)(APP_NAME, item[2])
                elif kind == "finished":
                    self._on_finished(item[1])
                elif kind == "update":
                    self._on_update_result(item[1], item[2], item[3])
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _on_finished(self, sm: dict):
        try:
            self._finish_run_ui(sm)
        finally:
            self.runlog.close()

    def _finish_run_ui(self, sm: dict):
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self._restore_links()
        text = f"Finished: {sm['done']} saved, {sm['skipped']} skipped, {sm['failed']} failed."
        if sm["stopped"]:
            text = "Stopped. " + text
        self.status_lbl.configure(text=text)
        self._append_log(text, "ok" if not sm["failed"] else "warn")
        if sm.get("saved"):
            self._pending_select = sm["saved"][0]
            self.btn_view.configure(fg_color=ui.ACCENT, hover_color=ui.ACCENT_HOVER, text_color="#ffffff",
                                    text=f"View results ({len(sm['saved'])} new)")
        if sm.get("capped"):
            self._append_log(f"{sm['capped']} video(s) were left for a later run (Settings → Videos per run).",
                             "warn")
        if sm.get("blocked"):
            self.s["last_blocked"] = time.time()
            self.persist()
            self.show_banner("YouTube is limiting requests from your internet connection, so the app stopped. "
                             "Your YouTube account isn't involved (the app never signs in). Wait several hours "
                             "before using links again, or use caption files.", "error",
                             "Use caption files", self.show_caption_help)
            messagebox.showwarning(
                APP_NAME, "YouTube is limiting requests from this internet connection, so the app stopped right "
                          "away instead of retrying.\n\nYour YouTube account isn't involved: the app never signs "
                          "in.\n\nWait several hours (or until tomorrow) before using links again. Caption "
                          "files from YouTube Studio work in the meantime.")
        elif sm.get("needs_file"):
            n = len(sm["needs_file"])
            self.show_banner(f"{n} video{'s' if n != 1 else ''} can only be watched while signed in (age-restricted "
                             "or members-only). The app never signs in, so use the caption file from YouTube "
                             "Studio for those.", "warn", "How to get caption files", self.show_caption_help)
        if sm["api_key_problem"]:
            if messagebox.askyesno(APP_NAME, "Gemini rejected your API key.\n\nEnter a new one now?"):
                self.open_wizard("gemini")
        elif sm["error"]:
            messagebox.showerror(APP_NAME, sm["error"])


def config_log_tags(box):
    for tag, pair in (("warn", ui.WARN), ("error", ui.ERROR), ("ok", ui.OK)):
        box.tag_config(tag, foreground=ui.pick(pair))


def append_log_line(box, line: str, level: str):
    box.configure(state="normal")
    if level in ("warn", "error", "ok"):
        box.insert("end", line + "\n", level)
    else:
        box.insert("end", line + "\n")
    box.see("end")
    box.configure(state="disabled")


class LogWindow(ctk.CTkToplevel):
    """The Activity log in its own resizable window (e.g. on a second screen). Updates live."""

    def __init__(self, app: App):
        super().__init__(app)
        self.app = app
        self.title(f"{APP_NAME} - Activity")
        ui.apply_window_icon(self)
        ui.fit_window(self, 820, 520, 420, 260)
        self.configure(fg_color=ui.CONTENT_BG)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 8))
        bar.grid_columnconfigure(0, weight=1)
        ui.label(bar, "Activity (updates live)", 15, "bold").grid(row=0, column=0, sticky="w")
        ui.button(bar, "Open log folder", app.open_log_folder, kind="secondary", height=30).grid(row=0, column=1)
        self.box = ctk.CTkTextbox(self, state="disabled", font=ui.mono(12), wrap="word")
        self.box.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.config_tags()
        for line, level in app._log_lines:
            append_log_line(self.box, line, level)
        self.after(250, self.lift)

    def config_tags(self):
        config_log_tags(self.box)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
