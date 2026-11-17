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
import transcribe  # noqa: E402
import usage  # noqa: E402
import worklist  # noqa: E402
from version import GITHUB_REPO  # noqa: E402
from wizard import CaptionFileHelp, SetupWizard  # noqa: E402

APP_NAME = ui.APP_NAME
NO_PRESET = "(unsaved / custom)"
NO_BACKUP = "(none)"
COOLDOWN_HOURS = 6
MIN_W, MIN_H = 900, 520  # smallest window (logical px): fits 1366x768 laptops at 125% scaling
SPLIT_TOP_MIN, SPLIT_BOTTOM_MIN = 64, 200  # Run page: smallest links area / controls + Activity area
MAX_LOG_LINES = 5000
NAV = [("run", "▶", "Run"), ("queue", "⏳", "Queue"), ("prompt", "✎", "Prompt"), ("results", "☰", "Results"),
       ("settings", "⚙", "Settings")]
THEN_TS_TEXT = "Also find the timestamps right after transcribing (untick for transcripts only)"
AUTO_TICK_MS = 60_000  # how often the Queue page looks for work while the app is open
PAGE_TEXT = {
    "run": ("Run", "Paste stream links (or add caption files) and press Start."),
    "prompt": ("Prompt", "Tell the AI what to look for and how each line should look."),
    "results": ("Results", "Read and copy what the app has saved."),
    "settings": ("Settings", "The essentials are at the top. Click a folded section to open it."),
    "queue": ("Queue", "Streams the app finishes on its own while it's open: new ones as soon as YouTube has "
                       "captions, and old ones a few per day."),
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
        self.discard_event = threading.Event()  # set with Stop = delete the unfinished parts
        self._tr_thread: threading.Thread | None = None
        self._run_whisper = False
        self.worker: threading.Thread | None = None
        self.wizard: SetupWizard | None = None
        self.pages: dict[str, ctk.CTkFrame] = {}
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.current_page = ""
        self._last_status_check = 0.0
        self._result_paths: list[str] = []
        self._result_buttons: list[ctk.CTkButton] = []
        self._selected_result = ""
        self._ts_tags: list[str] = []
        self._pending_select = ""
        self.caption_files: list[str] = []
        self._log_lines: list[tuple[str, str]] = []  # (line, level) of the current/last run
        self.log_window: LogWindow | None = None
        self.runlog = runlog.RunLog()
        self._split_placed = False
        self._split_h = 0
        self._auto_run = False  # the current run was started by the Queue page
        self._transcribe_only_run = False
        self._transcribing = False
        self._backlog_listing = False
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
        self.after(2500, self._startup_unfinished_transcripts)
        self.after(5000, self._auto_tick)

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
        self.v_backup = sv(value=s.get("backup_model", "") or NO_BACKUP)
        self.v_merge = sv(value=n(s.get("merge_seconds", 15)))
        self.v_auto = bv(value=bool(s.get("auto_runs", True)))
        self.v_backlog_url = sv(value="")
        self.v_backlog_day = sv(value=n(s.get("backlog_per_day", worklist.BACKLOG_PER_DAY)))
        self.v_whisper = sv(value=s.get("whisper_model") or transcribe.DEFAULT_MODEL)
        self.v_then_ts = bv(value=bool(s.get("transcribe_then_timestamps", True)))
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
        builders = {"run": self._build_run, "queue": self._build_queue, "prompt": self._build_prompt,
                    "results": self._build_results,
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
        ui.label(sb, f"   Version {ui.VERSION}", 11, color=ui.MUTED).grid(row=r + 1, column=0, sticky="ew",
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
        if key == "queue":
            self.refresh_queue()
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

        # links on top, controls + Activity below, with a grab handle between them to drag up or down
        self._run_body = body
        body.grid_rowconfigure(1, weight=0)
        body.grid_rowconfigure(3, weight=1)
        top = ctk.CTkFrame(body, fg_color="transparent", corner_radius=0, height=200)
        top.grid(row=1, column=0, sticky="nsew")
        top.grid_propagate(False)
        top.grid_columnconfigure(0, weight=1)
        top.grid_rowconfigure(0, weight=1)
        self._links_area = top
        links = top
        self._build_split_handle(body).grid(row=2, column=0, sticky="ew")
        bottom = ctk.CTkFrame(body, fg_color="transparent", corner_radius=0)
        bottom.grid(row=3, column=0, sticky="nsew")
        bottom.grid_columnconfigure(0, weight=1)
        bottom.grid_rowconfigure(2, weight=1)
        body.bind("<Configure>", self._on_split_configure, add="+")

        outer, inner = ui.card(links, "Stream links", "One per line: streams, playlists or channels.")
        outer.grid(row=0, column=0, sticky="nsew")
        self._links_card = outer
        inner.grid_columnconfigure(0, weight=1)
        inner.grid_rowconfigure(0, weight=1)  # the links box grows and shrinks with the handle
        self.urls_box = ctk.CTkTextbox(inner, height=60, font=ui.font(14), border_width=1,
                                       border_color=ui.CARD_BORDER)
        self.urls_box.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self.urls_box.insert("1.0", self.s.get("last_urls", ""))
        self.links_off_lbl = ui.label(inner, "Links are turned off: 'Caption files only' is on in Settings.", 13,
                                      color=ui.WARN)
        self.links_off_lbl.grid(row=1, column=0, sticky="w", pady=(6, 0))
        # one row: caption files on the left, the skip option on the right
        ff = ctk.CTkFrame(inner, fg_color="transparent")
        ff.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ff.grid_columnconfigure(3, weight=1)
        ui.button(ff, "Add caption files…", self.add_caption_files, kind="secondary", height=32).grid(
            row=0, column=0)
        ui.button(ff, "How?", self.show_caption_help, kind="ghost", width=50, height=32).grid(row=0, column=1,
                                                                                            padx=4)
        ui.button(ff, "Transcribe a recording…", self.transcribe_recording, kind="secondary", height=32).grid(
            row=0, column=5, padx=(10, 0))
        self.files_lbl = ui.label(ff, "", 13, color=ui.MUTED, wrap=320)
        self.files_lbl.grid(row=0, column=2, sticky="w", padx=4)
        self.btn_clear_files = ui.button(ff, "Clear", self.clear_caption_files, kind="ghost", width=60, height=32)
        self.btn_clear_files.grid(row=0, column=3, sticky="w")
        ctk.CTkCheckBox(ff, text="Skip streams that already have results", variable=self.v_skip,
                        font=ui.font(13)).grid(row=0, column=4, sticky="e")
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
        self.progress.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.status_lbl = ui.label(prog, "Ready.", 13, color=ui.MUTED)
        self.status_lbl.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.usage_lbl = ui.label(prog, "", 12, color=ui.MUTED)
        self.usage_lbl.grid(row=1, column=1, sticky="e", pady=(6, 0), padx=(12, 0))

        lo, li = ui.card(bottom, "Activity")
        lo.grid(row=2, column=0, sticky="nsew")
        bar = ctk.CTkFrame(lo, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="e", padx=16, pady=(12, 0))
        ui.button(bar, "Open log folder", self.open_log_folder, kind="ghost", height=28,
                  font=ui.font(13)).pack(side="left")
        ui.button(bar, "Pop out ⧉", self.open_log_window, kind="secondary", height=28,
                  font=ui.font(13)).pack(side="left", padx=(6, 0))
        li.grid_columnconfigure(0, weight=1)
        li.grid_rowconfigure(0, weight=1)
        self.log_box = ctk.CTkTextbox(li, state="disabled", font=ui.mono(12), fg_color="transparent", wrap="word")
        self.log_box.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self._config_log_tags()

    # ---- the grab handle between the links and the Activity log
    def _build_split_handle(self, parent):
        """A full-width strip with a visible grip. Drag it to resize; double-click to shrink/restore the links."""
        bar = ctk.CTkFrame(parent, fg_color="transparent", height=26, cursor="sb_v_double_arrow")
        bar.grid_columnconfigure((0, 3), weight=1)
        bar.grid_rowconfigure(0, weight=1)
        bar.grid_propagate(False)
        grip = ctk.CTkFrame(bar, width=140, height=8, corner_radius=4, fg_color=ui.GRIP,
                            cursor="sb_v_double_arrow")
        grip.grid(row=0, column=1, pady=9)
        tip = ui.label(bar, "drag to resize", 11, color=ui.MUTED, cursor="sb_v_double_arrow")
        tip.grid(row=0, column=2, padx=(10, 0))
        self._grip = grip
        for w in (bar, grip, tip):
            w.bind("<Button-1>", self._split_press, add="+")
            w.bind("<B1-Motion>", self._split_drag, add="+")
            w.bind("<ButtonRelease-1>", self._on_split_moved, add="+")
            w.bind("<Double-Button-1>", self._split_toggle, add="+")
            w.bind("<Enter>", lambda e: grip.configure(fg_color=ui.ACCENT), add="+")
            w.bind("<Leave>", lambda e: grip.configure(fg_color=ui.GRIP), add="+")
        return bar

    def _split_limits(self) -> tuple[int, int]:
        sc = self._scale()
        lo = int(SPLIT_TOP_MIN * sc)
        room = self._run_body.winfo_height() - int((SPLIT_BOTTOM_MIN + 26) * sc)
        if getattr(self, "_banner_visible", False):
            room -= self.banner.winfo_height()
        return lo, max(lo, room)

    def _split_y(self) -> int:
        """Height of the links area in screen pixels."""
        return self._split_h

    def _place_split(self, y: int):
        lo, hi = self._split_limits()
        self._split_h = max(lo, min(hi, int(y)))
        self._links_area.configure(height=max(1, round(self._split_h / self._scale())))

    def _split_press(self, event):
        self._drag_from = (event.y_root, self._split_h)

    def _split_drag(self, event):
        y0, h0 = getattr(self, "_drag_from", (event.y_root, self._split_h))
        self._place_split(h0 + event.y_root - y0)

    def _split_toggle(self, event=None):
        lo, _ = self._split_limits()
        if self._split_h > lo + 4:
            self._split_restore_to = self._split_h
            self._place_split(lo)
        else:
            self._place_split(getattr(self, "_split_restore_to", 0) or self._default_split())
        self._on_split_moved()

    def _default_split(self) -> int:
        return self._links_card.winfo_reqheight() + int(4 * self._scale())

    def _on_split_configure(self, event=None):
        """Put the handle where it was last left (or just below the links) once the page has a real size,
        and keep it inside the window when the window is resized."""
        h = self._run_body.winfo_height()
        if h < 100:
            return
        if self._split_placed:
            self._place_split(self._split_h)
            return
        self._split_placed = True
        try:
            frac = float(self.s.get("run_split") or 0)
        except (TypeError, ValueError):
            frac = 0.0
        self._place_split(int(h * frac) if 0.05 <= frac <= 0.9 else min(self._default_split(), int(h * 0.55)))

    def _on_split_moved(self, event=None):
        if self.worker and self.worker.is_alive():
            self._split_user_moved = True  # keep where they put it during a run
            return
        h = self._run_body.winfo_height()
        if h >= 100 and self._split_h > 0:
            self.s["run_split"] = round(self._split_h / h, 3)

    def _collapse_links(self):
        """During a run, give the Activity log most of the page (drag the handle to see the links again)."""
        self._split_user_moved = False
        self._split_before = self._split_y()
        self._place_split(int(SPLIT_TOP_MIN * self._scale()))

    def _restore_links(self):
        if self._split_before and not self._split_user_moved:
            self._place_split(self._split_before)
        self._split_before = 0

    # ------------------------------------------------------------------ Queue page
    def _build_queue(self, page):
        sc = ui.ScrollFrame(page, fg_color="transparent")
        sc.grid(row=1, column=0, sticky="nsew", padx=18, pady=(12, 16))
        sc.grid_columnconfigure(0, weight=1)

        o, i = ui.card(sc, "Automatic runs")
        o.grid(row=0, column=0, sticky="ew", padx=14, pady=(0, 14))
        ctk.CTkSwitch(i, text="Run the lists below on their own while the app is open", variable=self.v_auto,
                      font=ui.font(14), command=self._on_auto_toggle).grid(row=0, column=0, columnspan=2, sticky="w")
        self.queue_status = ui.label(i, "", 13, color=ui.MUTED, wrap=700)
        self.queue_status.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        o, i = ui.card(sc, "Waiting for captions",
                       "Streams that were still live, or had no captions yet, when you ran them. Checked about once "
                       "an hour; dropped after 2 days.")
        o.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 14))
        bf = ctk.CTkFrame(i, fg_color="transparent")
        bf.grid(row=0, column=0, columnspan=2, sticky="w")
        ui.button(bf, "Check now", self.check_waiting_now, kind="secondary", height=32).pack(side="left")
        ui.button(bf, "Clear list", self.clear_waiting, kind="ghost", height=32).pack(side="left", padx=8)
        self.waiting_rows = ctk.CTkFrame(i, fg_color="transparent")
        self.waiting_rows.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.waiting_rows.grid_columnconfigure(0, weight=1)

        o, i = ui.card(sc, "Backlog",
                       "Older streams from a channel or playlist, done a few per day so the free limits last. Waiting "
                       "streams always go first. Streams that already have a result are left out.")
        o.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 14))
        af = ctk.CTkFrame(i, fg_color="transparent")
        af.grid(row=0, column=0, columnspan=2, sticky="ew")
        af.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(af, textvariable=self.v_backlog_url, height=36, font=ui.font(14)).grid(row=0, column=0,
                                                                                           sticky="ew")
        self.btn_backlog_add = ui.button(af, "Add channel or playlist", self.add_backlog, width=200)
        self.btn_backlog_add.grid(row=0, column=1, padx=(10, 0))
        df = ctk.CTkFrame(i, fg_color="transparent")
        df.grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ctk.CTkEntry(df, textvariable=self.v_backlog_day, width=70, height=32, font=ui.font(14)).pack(side="left")
        ui.label(df, "  backlog streams per day", 14).pack(side="left")
        self.backlog_rows = ctk.CTkFrame(i, fg_color="transparent")
        self.backlog_rows.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.backlog_rows.grid_columnconfigure(0, weight=1)

    def refresh_queue(self):
        for frame in (self.waiting_rows, self.backlog_rows):
            for w in frame.winfo_children():
                w.destroy()
        items = worklist.waiting(self.s)
        if not items:
            ui.label(self.waiting_rows, "Nothing waiting.", 13, color=ui.MUTED).grid(row=0, column=0, sticky="w")
        for r, w in enumerate(items):
            nxt = float(w.get("next_check", 0))
            when = "next check: soon" if nxt <= time.time() else f"next check {time.strftime('%H:%M', time.localtime(nxt))}"
            ui.label(self.waiting_rows, f"{w.get('title') or w['id']}   ·   {when}", 13, wrap=560).grid(
                row=r, column=0, sticky="w", pady=2)
            ui.button(self.waiting_rows, "Remove", lambda v=w["id"]: self.remove_waiting(v), kind="ghost", width=70,
                      height=26).grid(row=r, column=1)
        sources = worklist.backlog(self.s)
        if not sources:
            ui.label(self.backlog_rows, "No backlog yet. Paste a channel or playlist link above.", 13,
                     color=ui.MUTED).grid(row=0, column=0, sticky="w")
        for r, b in enumerate(sources):
            left = len(b.get("pending", []))
            done, failed = int(b.get("done", 0)), int(b.get("failed", 0))
            text = f"{b.get('label') or b['url']}   ·   {done} done" + (f", {failed} failed" if failed else "") + \
                   f", {left} to go"
            ui.label(self.backlog_rows, text, 13, wrap=560).grid(row=r, column=0, sticky="w", pady=2)
            ui.button(self.backlog_rows, "Remove", lambda u=b["url"]: self.remove_backlog(u), kind="ghost", width=70,
                      height=26).grid(row=r, column=1)
        self.queue_status.configure(text=self._queue_status_text())

    def _queue_status_text(self) -> str:
        s = self.s
        if not self.v_auto.get():
            return "Off: nothing runs on its own. Lists are kept."
        if s.get("files_only"):
            return "Paused: 'Caption files only' is on in Settings, so YouTube isn't contacted."
        if worklist.paused_today(s):
            return "Paused until midnight Pacific time: Gemini's daily limit was reached."
        since = time.time() - float(s.get("last_blocked") or 0)
        if since < COOLDOWN_HOURS * 3600:
            return f"Paused for about {int((COOLDOWN_HOURS * 3600 - since) // 3600) + 1} h: YouTube limited this " \
                   "connection recently."
        left = worklist.backlog_left_today(s)
        parts = [f"{len(worklist.waiting(s))} waiting"]
        if worklist.backlog(s):
            parts.append(f"{left} more backlog stream{'s' if left != 1 else ''} today")
        return "On: " + ", ".join(parts) + ". Keep the app open (minimized is fine)."

    def _on_auto_toggle(self):
        self.s["auto_runs"] = bool(self.v_auto.get())
        self.persist()
        self.refresh_queue()

    def check_waiting_now(self):
        worklist.check_all_now(self.s)
        self.refresh_queue()
        self._maybe_auto_run(manual=True)

    def clear_waiting(self):
        if worklist.waiting(self.s) and messagebox.askyesno(APP_NAME, "Remove every stream from the waiting list?"):
            self.s["waiting"] = []
            self.persist()
            self.refresh_queue()

    def remove_waiting(self, vid: str):
        worklist.remove_waiting(self.s, [vid])
        self.persist()
        self.refresh_queue()

    def remove_backlog(self, url: str):
        if messagebox.askyesno(APP_NAME, "Remove this channel/playlist from the backlog? Results already saved "
                                         "are kept."):
            worklist.remove_backlog(self.s, url)
            self.persist()
            self.refresh_queue()

    def add_backlog(self):
        line = self.v_backlog_url.get().strip()
        if not line or self._backlog_listing:
            return
        if self.collect() is None:
            return
        if self.s.get("files_only"):
            messagebox.showinfo(APP_NAME, "'Caption files only' is on in Settings, so the app can't list a channel.")
            return
        kind, _ = core.classify_url(line)
        if kind not in ("channel", "playlist"):
            messagebox.showinfo(APP_NAME, "Paste a channel or playlist link here. Single streams go on the Run page.")
            return
        try:
            cfg = core.RunConfig.from_settings(self.s)
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        self._backlog_listing = True
        self.btn_backlog_add.configure(state="disabled", text="Listing videos…")

        def work():
            try:
                url, videos = core.list_videos(cfg, line)
                self.q.put(("backlog_listed", url, videos))
            except Exception as e:  # noqa: BLE001
                self.q.put(("backlog_error", str(e), isinstance(e, core.YouTubeBlocked)))

        threading.Thread(target=work, daemon=True).start()

    def _on_backlog_listed(self, url: str, videos: list):
        self._backlog_listing = False
        self.btn_backlog_add.configure(state="normal", text="Add channel or playlist")
        out = self.s.get("output_dir", "")
        have = [v for v, _ in videos if core.find_existing_output(out, v)]
        label = url.replace(core.YT_BASE + "/", "")
        n = worklist.add_backlog(self.s, url, label, videos, skip_ids=have)
        self.v_backlog_url.set("")
        self.persist()
        self.refresh_queue()
        messagebox.showinfo(APP_NAME, f"Added {n} stream{'s' if n != 1 else ''} from {label} to the backlog"
                            + (f" ({len(have)} already had results)." if have else ".")
                            + f"\n\nThe app does {worklist.per_day(self.s)} per day while it's open.")

    def _on_backlog_error(self, msg: str, blocked: bool):
        self._backlog_listing = False
        self.btn_backlog_add.configure(state="normal", text="Add channel or playlist")
        if blocked:
            self.s["last_blocked"] = time.time()
            self.persist()
            msg = "YouTube is limiting requests from this connection right now. Try again in a few hours."
        messagebox.showerror(APP_NAME, msg)

    # ---- the automatic runs
    def _auto_tick(self):
        self.after(AUTO_TICK_MS, self._auto_tick)
        try:
            self._maybe_auto_run()
        except Exception as e:  # noqa: BLE001  (never let the timer die)
            self._append_log(f"Queue: {e}", "warn")

    def _busy(self) -> bool:
        return bool((self.worker and self.worker.is_alive()) or self._transcribing
                    or (self._tr_thread and self._tr_thread.is_alive()))

    def _maybe_auto_run(self, manual: bool = False):
        s = self.s
        for w in worklist.expire_waiting(s):
            self._append_log(f"Stopped waiting for captions on '{w.get('title') or w['id']}' after 2 days. "
                             "Use a caption file or a transcript for it.", "warn")
        if self.current_page == "queue":
            self.refresh_queue()
        if not (self.v_auto.get() or manual) or self._busy() or s.get("files_only") or not s.get("api_key"):
            return False
        if time.time() - float(s.get("last_blocked") or 0) < COOLDOWN_HOURS * 3600:
            return False
        ids = worklist.plan_auto_run(s, int(s.get("max_per_run") or 0))
        if not ids:
            return False
        return self.start(auto_ids=ids)

    # ------------------------------------------------------------------ Prompt page
    def _build_prompt(self, page):
        sc = ui.ScrollFrame(page, fg_color="transparent")
        sc.grid(row=1, column=0, sticky="nsew", padx=18, pady=(12, 16))
        sc.grid_columnconfigure(0, weight=1)
        fw = self.form_widgets

        # presets
        o, i = ui.card(sc, "Preset", "Changes below aren't kept in the preset until you save it.")
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
        o, i = ui.card(sc, "What to look for", "Describe it like you'd explain it to a person. Click ? for "
                                               "examples.")
        o.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 14))
        r = 0

        def field(row, text, key, help_text=None, width=None, track=True):
            lf = ctk.CTkFrame(i, fg_color="transparent")
            lf.grid(row=row, column=0, sticky="nw", padx=(0, 16), pady=(12, 0))
            ui.label(lf, text, 14).pack(side="left")
            e = ctk.CTkEntry(i, textvariable=self.fv[key], width=width or 200, height=36, font=ui.font(14))
            e.grid(row=row, column=1, sticky="w" if width else "ew", pady=(8, 0))
            if track:
                fw.append(e)
            if help_text:
                h = ui.hint(i, help_text)
                h.grid(row=row + 1, column=1, sticky="w")
                ui.help_toggle(lf, h).pack(side="left", padx=(6, 0))
                return row + 2
            return row + 1

        r = field(r, "Show / channel name", "show", "Optional. Example: The Bible Guy")
        r = field(r, "About the show", "description", "Optional, one line. Example: an atheist call-in show")
        r = field(r, "What should it find? *", "find",
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

        # layout (rarely changed, so folded away)
        o, i, _ = ui.section(sc, "Output format", "Separator, title line, blank lines and YouTube chapters.")
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
        h = ui.hint(i, "Timestamps in a description or comment are clickable either way. Chapters (the sections "
                       "on the video's progress bar) also need a 00:00 first line and at least 3 timestamps, each "
                       "10+ seconds apart.", wrap=700)
        h.grid(row=4, column=0, columnspan=2, sticky="w", pady=(2, 0))
        ui.help_toggle(chf, h).pack(side="left", padx=(4, 0))

        # preview
        o, i, self._open_preview = ui.section(
            sc, "Instructions sent to the AI", "Built from the boxes above. Open to read it, or tick Advanced to "
                                               "edit it by hand ({min_minutes} becomes the minimum length).")
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
        self.results_list = ui.ScrollFrame(lo, fg_color="transparent")
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
        self.btn_clips = ui.button(vb, "Save clip list (.csv)", self.save_clip_list, kind="secondary",
                                   state="disabled")
        self.btn_clips.pack(side="left", padx=10)
        self.view_hint = ui.label(vo, "", 12, color=ui.MUTED)
        self.view_hint.grid(row=3, column=0, sticky="w", padx=20, pady=(0, 14))
        self.view_box = ctk.CTkTextbox(vo, wrap="word", font=ui.mono(13), state="disabled", border_width=1,
                                       border_color=ui.CARD_BORDER)
        self.view_box.grid(row=2, column=0, sticky="nsew", padx=20, pady=(12, 20))

    # ------------------------------------------------------------------ Settings page
    def _build_settings(self, page):
        sc = ui.ScrollFrame(page, fg_color="transparent")
        sc.grid(row=1, column=0, sticky="nsew", padx=18, pady=(12, 16))
        sc.grid_columnconfigure(0, weight=1)
        row = [0]

        def add_card(title, sub=None, folded=False):
            if folded:
                o, i, _ = ui.section(sc, title, sub)
            else:
                o, i = ui.card(sc, title, sub)
            o.grid(row=row[0], column=0, sticky="ew", padx=14, pady=(0, 14))
            row[0] += 1
            return i

        def srow(inner, r, text, widget, help_text=None, sticky="w"):
            """Label (+ a '?' that shows the help line) on the left, the control on the right."""
            lf = ctk.CTkFrame(inner, fg_color="transparent")
            lf.grid(row=r, column=0, sticky="nw", padx=(0, 20), pady=(12, 0))
            ui.label(lf, text, 14).pack(side="left")
            widget.grid(row=r, column=1, sticky=sticky, pady=(8, 0))
            if help_text:
                h = ui.hint(inner, help_text)
                h.grid(row=r + 1, column=1, sticky="w")
                ui.help_toggle(lf, h).pack(side="left", padx=(6, 0))
                return r + 2
            return r + 1

        def num_entry(inner, var):
            return ctk.CTkEntry(inner, textvariable=var, width=90, height=34, font=ui.font(14))

        # ---- essentials (always open)
        i = add_card("Gemini AI")
        kf = ctk.CTkFrame(i, fg_color="transparent")
        kf.grid_columnconfigure(0, weight=1)
        self.key_entry = ctk.CTkEntry(kf, textvariable=self.v_key, show="•", height=36, font=ui.font(14))
        self.key_entry.grid(row=0, column=0, sticky="ew")
        ctk.CTkCheckBox(kf, text="Show", variable=self.v_show_key, width=70, command=self.toggle_key).grid(
            row=0, column=1, padx=10)
        ui.button(kf, "Test", self.test_key, kind="secondary", width=70).grid(row=0, column=2)
        r = srow(i, 0, "API key", kf, "Get one at aistudio.google.com/apikey, or use Run setup again (below).",
                 sticky="ew")
        r = srow(i, r, "AI model", ctk.CTkComboBox(i, values=ui.MODELS, variable=self.v_model, width=260,
                                                    height=34), "You can also type any Gemini model name.")
        self.add_model_note(i).grid(row=r, column=1, sticky="w", pady=(4, 0))
        self.usage_lbl_settings = ui.label(i, "", 12, color=ui.MUTED, wrap=560)
        self.usage_lbl_settings.grid(row=r + 1, column=1, sticky="w", pady=(6, 0))

        i = add_card("Where results go")
        of = ctk.CTkFrame(i, fg_color="transparent")
        of.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(of, textvariable=self.v_out, height=36, font=ui.font(14)).grid(row=0, column=0, sticky="ew")
        ui.button(of, "Browse...", self.browse_out, kind="secondary", width=100).grid(row=0, column=1, padx=(10, 0))
        ui.button(of, "Open", self.open_output, kind="secondary", width=70).grid(row=0, column=2, padx=(10, 0))
        r = srow(i, 0, "Output folder", of, sticky="ew")
        ctk.CTkCheckBox(i, text="Also save the raw captions (.transcript.txt)", variable=self.v_raw,
                        font=ui.font(13)).grid(row=r, column=1, sticky="w", pady=(12, 0))

        # ---- rarely changed (folded)
        i = add_card("Which videos", "Minimum stream length, channel tab, how many videos, caption language.",
                     folded=True)
        r = srow(i, 0, "Skip streams shorter than", self._with_unit(i, self.v_min_stream, "minutes"))
        r = srow(i, r, "Channel links use", ctk.CTkOptionMenu(i, values=["Streams", "Videos"], variable=self.v_tab,
                                                               width=130, height=34),
                 "Which tab of a channel to take videos from.")
        r = srow(i, r, "From channels / playlists, take", self._with_unit(i, self.v_max, "newest videos (0 = all)"))
        srow(i, r, "Caption language", ctk.CTkEntry(i, textvariable=self.v_lang, width=90, height=34),
             "Language code of the captions to read, e.g. en, es, de.")

        i = add_card("YouTube safety", "No login is ever used. Pauses, a per-run limit, and a caption-files-only "
                                       "mode keep requests slow and polite.", folded=True)
        wf = ctk.CTkFrame(i, fg_color="transparent")
        num_entry(wf, self.v_dmin).pack(side="left")
        ui.label(wf, "  to  ", 14).pack(side="left")
        num_entry(wf, self.v_dmax).pack(side="left")
        ui.label(wf, "  seconds", 14).pack(side="left")
        r = srow(i, 0, "Wait between videos", wf, "A random pause between videos. Lower = faster, but more likely "
                                                  "to get limited.")
        r = srow(i, r, "Videos per run", self._with_unit(i, self.v_max_run, "at most (0 = no limit)"),
                 "Big batches are what get noticed. Spread them out over the day.")
        cf = ctk.CTkFrame(i, fg_color="transparent")
        ctk.CTkCheckBox(cf, text="Caption files only (never contact YouTube)", variable=self.v_files_only,
                        font=ui.font(13), command=self._refresh_files_only).pack(side="left")
        ui.button(cf, "How to get them", self.show_caption_help, kind="ghost", height=30).pack(side="left", padx=8)
        r = srow(i, r, "Mode", cf, "If YouTube ever says 'too many requests' or asks you to confirm you're not a "
                                  "bot, the app stops on its own. Wait several hours, or use caption files.")

        i = add_card("AI requests", "Retries, backup model, and how captions are packed to save your free quota.",
                     folded=True)
        r = srow(i, 0, "Retries when Gemini is busy", num_entry(i, self.v_retries),
                 "Every attempt counts against a free key's daily requests, even a 'busy' answer, so keep this "
                 "low (2-3).")
        r = srow(i, r, "Backup model", ctk.CTkComboBox(i, values=[NO_BACKUP] + ui.MODELS, variable=self.v_backup,
                                                        width=260, height=34),
                 "Used when the main model stays overloaded (for that stream), or for the rest of the run once its "
                 "daily limit is reached. Each model has its own free daily limit.")
        srow(i, r, "Merge captions into", self._with_unit(i, self.v_merge, "second blocks (0 = off)"),
             "Fewer, longer lines use fewer tokens. Timestamps can be up to this many seconds early. 15 is a good "
             "balance; up to 60 is allowed.")

        i = add_card("Transcribing recordings", "Turn a recording on this PC into captions with Whisper, with no "
                                                "wait for YouTube's captions.", folded=True)
        ctk.CTkCheckBox(i, text=THEN_TS_TEXT, variable=self.v_then_ts, font=ui.font(13)).grid(
            row=2, column=1, sticky="w", pady=(12, 0))
        ui.button(i, "Open Transcripts folder", self.open_transcripts, kind="secondary").grid(
            row=3, column=1, sticky="w", pady=(10, 0))
        srow(i, 0, "Speech model", ctk.CTkOptionMenu(i, values=list(transcribe.MODELS), variable=self.v_whisper,
                                                       width=130, height=34),
             "base: " + transcribe.MODELS["base"][1] + ". small: " + transcribe.MODELS["small"][1] + ". Each is "
             "downloaded once from Hugging Face the first time it's used (base " + transcribe.MODELS["base"][0] +
             ", small " + transcribe.MODELS["small"][0] + "). Uses the Caption language above.")

        # ---- app
        i = add_card("App")
        r = srow(i, 0, "Appearance", ctk.CTkOptionMenu(i, values=["System", "Light", "Dark"],
                                                        variable=self.v_appearance, width=130, height=34,
                                                        command=self.change_appearance))
        af = ctk.CTkFrame(i, fg_color="transparent")
        ui.button(af, "Run setup again", lambda: self.open_wizard(), kind="secondary").pack(side="left")
        ui.button(af, "Open settings folder", lambda: ui.open_path(str(store.app_dir())),
                  kind="secondary").pack(side="left", padx=10)
        r = srow(i, r, "Setup", af)
        if updates.repo_ok(GITHUB_REPO):
            uf = ctk.CTkFrame(i, fg_color="transparent")
            ctk.CTkCheckBox(uf, text="Tell me when a new version is out", variable=self.v_check_updates,
                            font=ui.font(13)).pack(side="left")
            ui.button(uf, "Check now", lambda: self.check_updates(manual=True), kind="secondary",
                      width=110).pack(side="left", padx=12)
            r = srow(i, r, "Updates", uf, "Checks GitHub once a day for a newer version and shows a link. It never "
                                           "downloads or installs anything by itself.")
            hf = ctk.CTkFrame(i, fg_color="transparent")
            ui.label(hf, f"Version {ui.VERSION}", 14, color=ui.MUTED).pack(side="left")
            ui.button(hf, "Help & report a problem", lambda: ui.open_url(updates.help_page(GITHUB_REPO)),
                      kind="secondary").pack(side="left", padx=12)
            srow(i, r, "Version", hf)
        else:
            srow(i, r, "Version", ui.label(i, f"{ui.VERSION} (update notices are only in copies from the "
                                              "project's GitHub Releases page)", 14, color=ui.MUTED))

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
        self.refresh_usage()

    def refresh_usage(self):
        """'Gemini requests today' on the Run page and in Settings (starts over at midnight Pacific time)."""
        text = usage.describe(self.s)
        self.usage_lbl.configure(text=text)
        self.usage_lbl_settings.configure(text=text + ". Counts only this app's requests; free daily limits reset "
                                                      "at midnight Pacific time.")

    def note_gemini(self, event: str, model: str):
        """Called on the main thread for every Gemini request ("sent") and daily-limit answer ("out")."""
        if event == "sent":
            usage.record(self.s, model)
        elif event == "out":
            usage.mark_out(self.s, model)
        self.refresh_usage()

    def _refresh_files_only(self):
        on = bool(self.v_files_only.get())
        self.urls_box.configure(state="disabled" if on else "normal")
        if on:
            self.links_off_lbl.grid()
        else:
            self.links_off_lbl.grid_remove()

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
            self.files_lbl.configure(text="")
            self.btn_clear_files.grid_remove()
            return
        names = ", ".join(os.path.basename(f) for f in self.caption_files[:3])
        more = f" and {n - 3} more" if n > 3 else ""
        self.files_lbl.configure(text=f"{n} file{'s' if n != 1 else ''}: {names}{more}")
        self.btn_clear_files.grid()

    # ================================================================== transcribing a recording
    def _ask_choice(self, title, message, options, check=None, select=None):
        return ui.ask_choice(self, title, message, options, check, select)

    def _links_in_box(self) -> list[str]:
        if self.s.get("files_only") or self.v_files_only.get():
            return []
        lines = self.urls_box.get("1.0", "end").splitlines()
        return [ln for ln in lines if core.classify_url(ln)[0] in ("video", "playlist", "channel")]

    def _whisper_ready(self) -> bool:
        """Ask before the speech model's one-time download. False = the person said no."""
        if self.collect() is None:
            return False
        model = self.s.get("whisper_model") or transcribe.DEFAULT_MODEL
        if transcribe.model_downloaded(model):
            return True
        size = transcribe.MODELS.get(model, ("", ""))[0]
        return messagebox.askyesno(
            APP_NAME, f"Transcribing needs Whisper's '{model}' speech model ({size}). It's downloaded once from "
                      "Hugging Face (huggingface.co) and kept in the app's settings folder.\n\nDownload it now?")

    def transcribe_recording(self):
        if not transcribe.available():
            messagebox.showinfo(APP_NAME, "Transcribing isn't included in this copy of the app. Download the newest "
                                          "version from the project's Releases page.")
            return
        if self._busy():
            messagebox.showinfo(APP_NAME, "Wait for the current run to finish first (or press Stop).")
            return
        links = self._links_in_box()
        check = ("Also find the timestamps (untick for transcripts only)", self.v_then_ts)
        select = ("Speech model:", self.v_whisper, list(transcribe.MODELS),
                  "base is faster; small is more accurate (better with names), about half the speed")
        if links:
            n = len(links)
            what = "this link" if n == 1 else f"these {n} links"
            choice = self._ask_choice(
                "Transcribe", f"Transcribe {what} on this PC?\n\nThe app downloads just the audio (logged out) and "
                              "turns it into a transcript with Whisper. Transcripts are kept in the Transcripts "
                              "folder.",
                [("links", "Yes, transcribe " + ("the link" if n == 1 else "the links"), "primary"),
                 ("file", "Choose a file instead", "secondary"), ("cancel", "Cancel", "ghost")], check, select)
        else:
            choice = self._ask_choice(
                "Transcribe", "Choose a recording (video or audio file) to transcribe on this PC with Whisper.\n\n"
                              "Tip: paste links in the box first to transcribe those instead.",
                [("file", "Choose a file", "primary"), ("cancel", "Cancel", "ghost")], check, select)
        self.s["transcribe_then_timestamps"] = bool(self.v_then_ts.get())
        self.s["whisper_model"] = self.v_whisper.get() if self.v_whisper.get() in transcribe.MODELS else "base"
        if choice == "links":
            if self._whisper_ready():
                self.start(whisper=True)
            return
        if choice != "file":
            return
        exts = " ".join("*" + e for e in transcribe.MEDIA_EXTS)
        path = filedialog.askopenfilename(title="Choose a stream recording (video or audio)",
                                          filetypes=[("Recordings", exts), ("All files", "*.*")])
        if path:
            self._start_transcription(path)

    def _start_transcription(self, path: str):
        if not self._whisper_ready():
            return
        out = self.s["output_dir"]
        problem = core.check_writable(out)
        if problem:
            messagebox.showerror(APP_NAME, problem)
            return
        model = self.s.get("whisper_model") or transcribe.DEFAULT_MODEL
        self._transcribing = True
        self.stop_event.clear()
        self.discard_event.clear()
        self._clear_log()
        self.runlog.start([f"{APP_NAME} {ui.VERSION} - transcribing {os.path.basename(path)} "
                           f"({time.strftime('%Y-%m-%d %H:%M:%S')})", f"Model: {model}", ""])
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.hide_banner()
        self.progress.set(0)
        self._t_started = (time.time(), None)
        self._append_log(f"Transcribing {os.path.basename(path)} on this PC with Whisper ({model}). It's saved as it "
                         "goes, so if the app is closed it continues where it stopped next time.", "info")
        lang = self.s.get("caption_lang", "en")
        stop = self.stop_event

        def work():
            try:
                if not transcribe.model_downloaded(model):
                    self.q.put(("status", f"Downloading the '{model}' speech model (once)…"))
                self.q.put(("status", "Loading the speech model…"))
                m = transcribe.load_model(model)
                srt = transcribe.transcribe(path, out, model=m, model_name=model, language=lang, stop=stop,
                                            progress=lambda d, t: self.q.put(("tprogress", d, t)),
                                            log=lambda msg: self.q.put(("log", msg, "info")))
                self.q.put(("transcribed", path, str(srt)))
            except transcribe.Stopped:
                if self.discard_event.is_set():
                    transcribe.discard_partial(path, out)
                self.q.put(("transcribe_stopped", path))
            except Exception as e:  # noqa: BLE001
                self.q.put(("transcribe_error", path, str(e)))

        self._tr_thread = threading.Thread(target=work, daemon=True)
        self._tr_thread.start()

    def _on_tprogress(self, done: float, total):
        self.progress.set(done / total if total else 0)
        started, first_done = self._t_started
        if first_done is None:  # measure speed from where this session began (it may be resuming)
            self._t_started = (time.time(), done)
            self.status_lbl.configure(text=f"Transcribing: {core.fmt_ts(int(done))} of {core.fmt_ts(int(total or 0))}"
                                           " (time left shows after the first 10 minutes)")
            return
        rate = (done - first_done) / max(1.0, time.time() - started)
        left = f", about {int((total - done) / rate / 60) + 1} min left" if total and rate > 0 else ""
        self.status_lbl.configure(text=f"Transcribing: {core.fmt_ts(int(done))} of "
                                       f"{core.fmt_ts(int(total or 0))}{left}")

    def _end_transcription(self, text: str, level: str):
        if not self._transcribing:
            return  # already ended (Stop ends it at once; the background work catches up later)
        self._transcribing = False
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.status_lbl.configure(text=text.split(". ")[0] + ".")
        self._append_log(text, level)
        self.runlog.close()

    def _on_transcribed(self, path: str, srt: str):
        self.progress.set(1)
        if not self.v_then_ts.get():
            if srt not in self.caption_files:
                self.caption_files.append(srt)
            self._refresh_files_label()
            self._end_transcription(f"Transcript saved: {srt}. It's also listed under caption files above: press "
                                    "Start whenever you want its timestamps.", "ok")
            return
        self._end_transcription(f"Transcript saved: {srt}. Now finding the timestamps ...", "ok")
        if not self.start(only_files=[srt]):  # e.g. no API key yet: keep it ready on the Run page instead
            if srt not in self.caption_files:
                self.caption_files.append(srt)
            self._refresh_files_label()
            self._append_log("The transcript was added to the caption files above: press Start when ready.", "info")

    def _startup_unfinished_transcripts(self):
        try:
            items = transcribe.unfinished(self.s.get("output_dir", ""))
        except Exception:  # noqa: BLE001
            return
        if not items or not transcribe.available() or getattr(self, "_banner_visible", False):
            return
        info = items[0]
        name = os.path.basename(info["source"])
        total = info.get("duration")
        where = core.fmt_ts(int(info.get("done_until", 0))) + (f" of {core.fmt_ts(int(total))}" if total else "")
        self.show_banner(f"Transcribing {name} stopped at {where}. The part done so far is saved.", "warn",
                         "Continue", lambda: (self.hide_banner(), self._start_transcription(info["source"])))

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
            s["merge_seconds"] = min(60, num(self.v_merge, "Merge captions into", int))
            s["backlog_per_day"] = num(self.v_backlog_day, "Backlog streams per day", int)
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e))
            return None
        s["api_key"] = self.v_key.get().strip()
        s["model"] = self.v_model.get().strip()
        b = self.v_backup.get().strip()
        s["backup_model"] = "" if b == NO_BACKUP or b == s["model"] else b
        s["output_dir"] = self.v_out.get().strip()
        s["caption_lang"] = self.v_lang.get().strip() or "en"
        s["channel_tab"] = "streams" if self.v_tab.get() == "Streams" else "videos"
        s["skip_existing"] = bool(self.v_skip.get())
        s["save_transcript"] = bool(self.v_raw.get())
        s["appearance"] = self.v_appearance.get()
        s["files_only"] = bool(self.v_files_only.get())
        s["check_updates"] = bool(self.v_check_updates.get())
        s["auto_runs"] = bool(self.v_auto.get())
        s["transcribe_then_timestamps"] = bool(self.v_then_ts.get())
        s["whisper_model"] = self.v_whisper.get() if self.v_whisper.get() in transcribe.MODELS else "base"
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
            self._open_preview(True)  # hand-edited text: show it
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

        self.note_gemini("sent", model)

        def work():
            ok, msg = core.test_api_key(key, model)
            if "daily limit" in msg:
                self.q.put(("gemini", "out", model))
            self.q.put(("keytest", ok, msg))

        threading.Thread(target=work, daemon=True).start()

    def open_transcripts(self):
        d = os.path.join(self.v_out.get().strip(), "Transcripts")
        try:
            os.makedirs(d, exist_ok=True)
            ui.open_path(d)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Could not open the folder: {e}")

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
        n = self._link_timestamps(text, core.video_id_from_result(self._selected_result) if text else "")
        self.view_box.configure(state="disabled")
        state = "normal" if text else "disabled"
        for b in (self.btn_copy, self.btn_open_res, self.btn_show_res, self.btn_clips):
            b.configure(state=state)
        self.view_hint.configure(text="Click a time to open the stream at that moment." if n else "")

    def _link_timestamps(self, text: str, video_id: str) -> int:
        """Make each line's start time a link to that moment of the stream. Returns how many were linked."""
        box = self.view_box
        for tag in self._ts_tags:
            box.tag_delete(tag)
        self._ts_tags = []
        if not video_id:
            return 0
        box.tag_config("ts_link", foreground=ui.pick(ui.ACCENT), underline=True)
        for row, line in enumerate(text.splitlines(), 1):
            stripped = line.lstrip()
            m = core.RESULT_TS_RE.match(stripped)
            if not m:
                continue
            col = len(line) - len(stripped)
            tag = f"ts{len(self._ts_tags)}"
            url = f"https://www.youtube.com/watch?v={video_id}&t={core.ts_seconds(m.group(1))}s"
            a, b = f"{row}.{col}", f"{row}.{col + len(m.group(1))}"
            box.tag_add("ts_link", a, b)
            box.tag_add(tag, a, b)
            box.tag_bind(tag, "<Button-1>", lambda e, u=url: ui.open_url(u))
            box.tag_bind(tag, "<Enter>", lambda e: self._view_cursor("hand2"))
            box.tag_bind(tag, "<Leave>", lambda e: self._view_cursor(""))
            self._ts_tags.append(tag)
        return len(self._ts_tags)

    def _view_cursor(self, cursor: str):
        try:
            self.view_box._textbox.configure(cursor=cursor or "xterm")
        except Exception:  # noqa: BLE001
            pass

    def save_clip_list(self):
        path = self._selected_result
        if not path:
            return
        text = self.view_box.get("1.0", "end")
        rows = core.clip_rows(text, core.video_id_from_result(path))
        if not rows:
            messagebox.showinfo(APP_NAME, "This result has no timestamped segments to put in a clip list.")
            return
        csv_path = os.path.splitext(path)[0] + " - clips.csv"
        try:
            core.write_clip_csv(csv_path, rows)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Couldn't save the clip list: {e}")
            return
        self._append_log(f"Clip list saved: {csv_path}", "ok")
        if messagebox.askyesno(APP_NAME, f"Saved {len(rows)} clips to:\n{os.path.basename(csv_path)}\n\n"
                                         "It opens in Excel, Google Sheets or any spreadsheet app. Show it in its "
                                         "folder?"):
            ui.show_in_folder(csv_path)

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
    def start(self, auto_ids: list[str] | None = None, only_files: list[str] | None = None,
              whisper: bool = False) -> bool:
        """Start a run. auto_ids: video ids from the Queue page (no pop-ups; the links box is left alone).
        only_files: just these caption files (a fresh transcript). whisper: transcribe the links' audio here."""
        auto = auto_ids is not None
        transcribe_only = bool(whisper) and not self.v_then_ts.get()
        if self._busy():
            if not auto and self._transcribing:
                messagebox.showinfo(APP_NAME, "A recording is being transcribed. Wait for it, or press Stop.")
            return False
        if self.collect() is None:
            return False
        if not self.s["api_key"] and not transcribe_only:
            if not auto and messagebox.askyesno(APP_NAME, "You haven't added your Gemini API key yet. Set it up now?"):
                self.open_wizard("gemini")
            return False
        self.refresh_status()
        if self.s["prompt_mode"] == "guided" and not transcribe_only:
            errs = pb.validate_form(self.s["form"])
            if errs:
                if not auto:
                    self.show_page("prompt")
                    messagebox.showinfo(APP_NAME, "\n".join(errs))
                return False
        files_only = bool(self.s.get("files_only"))
        if auto:
            urls, files = "\n".join(auto_ids), []
        elif only_files is not None:
            urls, files = "", list(only_files)
        else:
            urls = "" if files_only else self.urls_box.get("1.0", "end")
            # "Transcribe the links" is about the links only; listed caption files wait for the next Start
            files = [] if whisper else [f for f in self.caption_files if os.path.isfile(f)]
        if not urls.strip() and not files:
            messagebox.showinfo(APP_NAME, "Add a caption file first." if files_only else
                                "Paste at least one YouTube link or add a caption file first.")
            return False
        if urls.strip() and not auto:
            since = time.time() - float(self.s.get("last_blocked") or 0)
            if since < COOLDOWN_HOURS * 3600:
                mins = int(since // 60)
                ago = f"{mins} minutes" if mins < 120 else f"{mins // 60} hours"
                if not messagebox.askyesno(
                        APP_NAME, f"YouTube limited this internet connection {ago} ago. Giving it a few more hours "
                                  f"is safer.\n\nCaption files still work any time.\n\nTry YouTube links anyway?"):
                    return False
        self.persist()
        try:
            cfg = core.RunConfig.from_settings(self.s)
        except ValueError as e:
            if not auto:
                messagebox.showerror(APP_NAME, str(e))
            return False
        cfg.use_whisper = bool(whisper)
        cfg.transcribe_only = transcribe_only
        self._transcribe_only_run = transcribe_only
        self._run_whisper = bool(whisper)
        self._auto_run = auto

        self.stop_event.clear()
        self.discard_event.clear()
        if only_files is None:  # a fresh transcript's run keeps the transcription's lines above it
            self._clear_log()
        self.runlog.start(self._log_header(cfg, urls, files))
        if whisper:
            self._append_log("Transcribing the links on this PC" + (" (transcripts only)" if transcribe_only else
                             ", then finding the timestamps") + ". Each transcript is saved in the Transcripts "
                             "folder as soon as it's done.", "info")
        if auto:
            self._append_log(f"Automatic run from the Queue page: {len(auto_ids)} stream(s).", "info")
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
            on_gemini=lambda event, model: self.q.put(("gemini", event, model)),
            discard=self.discard_event,
        )

        def work():
            summary = runner.run()
            self.q.put(("finished", summary))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        return True

    def stop(self):
        running = bool(self.worker and self.worker.is_alive())
        if self._transcribing or (running and self._run_whisper):
            choice = self._ask_choice(
                "Stop", "Stop transcribing?\n\nThe part transcribed so far can be kept, so the same recording or "
                        "link continues from there next time, or deleted. Finished transcripts and saved results "
                        "are always kept.",
                [("keep", "Stop, keep the part done", "primary"), ("discard", "Stop and delete it", "secondary"),
                 ("cancel", "Keep going", "ghost")])
            if choice not in ("keep", "discard"):
                return
            if choice == "discard":
                self.discard_event.set()
        self.stop_event.set()
        self.btn_stop.configure(state="disabled")
        self.status_lbl.configure(text="Stopping ...")
        if self._transcribing:  # the screen is freed at once; the background work notices Stop by itself
            self._end_transcription("Stopped." + (" The unfinished transcript was deleted." if
                                                  self.discard_event.is_set() else
                                                  " The part done so far is saved: transcribe the same file again "
                                                  "(or click Continue next time the app opens) to carry on."),
                                    "warn")

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
                elif kind == "gemini":
                    self.note_gemini(item[1], item[2])
                elif kind == "finished":
                    self._on_finished(item[1])
                elif kind == "update":
                    self._on_update_result(item[1], item[2], item[3])
                elif kind == "tprogress":
                    self._on_tprogress(item[1], item[2])
                elif kind == "transcribed":
                    self._on_transcribed(item[1], item[2])
                elif kind == "transcribe_stopped":
                    self._end_transcription("Stopped.", "warn")  # normally already shown by stop()
                elif kind == "transcribe_error":
                    self._end_transcription(f"Couldn't transcribe {os.path.basename(item[1])}: {item[2]}", "error")
                elif kind == "backlog_listed":
                    self._on_backlog_listed(item[1], item[2])
                elif kind == "backlog_error":
                    self._on_backlog_error(item[1], item[2])
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
        auto, self._auto_run = self._auto_run, False
        only, self._transcribe_only_run = self._transcribe_only_run, False
        text = f"Finished: {sm['done']} {'transcribed' if only else 'saved'}, {sm['skipped']} skipped, " \
               f"{sm['failed']} failed"
        if sm.get("waiting"):
            text += f", {sm['waiting']} waiting for captions"
        text += "."
        if sm["stopped"]:
            text = "Stopped. " + text
        if auto:
            text = "Automatic run: " + text
        self.status_lbl.configure(text=text)
        self._append_log(text, "ok" if not sm["failed"] else "warn")
        if only and sm.get("transcribed"):
            for srt in sm["transcribed"]:
                if srt not in self.caption_files:
                    self.caption_files.append(srt)
            self._refresh_files_label()
            self._append_log("The transcripts are listed under caption files above: press Start whenever you want "
                             "their timestamps.", "info")
        if not only:
            self._update_worklist(sm)
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
            if not auto:
                messagebox.showwarning(
                    APP_NAME, "YouTube is limiting requests from this internet connection, so the app stopped "
                              "right away instead of retrying.\n\nYour YouTube account isn't involved: the app never "
                              "signs in.\n\nWait several hours (or until tomorrow) before using links again. "
                              "Caption files from YouTube Studio work in the meantime.")
        elif sm.get("needs_file"):
            n = len(sm["needs_file"])
            self.show_banner(f"{n} video{'s' if n != 1 else ''} can only be watched while signed in (age-restricted "
                             "or members-only). The app never signs in, so use the caption file from YouTube "
                             "Studio for those.", "warn", "How to get caption files", self.show_caption_help)
        if sm["api_key_problem"]:
            if auto:
                self.v_auto.set(False)
                self.s["auto_runs"] = False
                self.persist()
                self.show_banner("Gemini rejected your API key, so automatic runs were turned off. Fix the key in "
                                 "Settings, then turn them back on on the Queue page.", "error")
            elif messagebox.askyesno(APP_NAME, "Gemini rejected your API key.\n\nEnter a new one now?"):
                self.open_wizard("gemini")
        elif sm["error"] and not auto:
            messagebox.showerror(APP_NAME, sm["error"])

    def _update_worklist(self, sm: dict):
        """Every run (automatic or not) updates the Queue page's lists."""
        before = {w["id"] for w in worklist.waiting(self.s)}
        worklist.apply_outcomes(self.s, sm.get("outcomes") or {}, dict(sm.get("waiting_list") or []))
        new = [v for v, _ in sm.get("waiting_list") or [] if v not in before]
        if new:
            self._append_log(f"{len(new)} stream(s) added to the waiting list. While the app is open it checks them "
                             "about once an hour and runs them when the captions appear (Queue page).", "info")
        if sm.get("daily_limit"):
            worklist.pause_today(self.s)
        self.persist()
        if self.current_page == "queue":
            self.refresh_queue()


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


def selftest(report: str) -> int:
    """`StreamTimestamper.exe --selftest report.txt`: check the packaged app has everything it needs, without
    opening a window or contacting anything. Used by the release build on the finished exe."""
    problems = []
    for mod in ("yt_dlp", "yt_dlp_ejs", "google.genai", "customtkinter", "PIL", "certifi", "tzdata"):
        try:
            __import__(mod)
        except Exception as e:  # noqa: BLE001
            problems.append(f"import {mod}: {e}")
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo("America/Los_Angeles")
    except Exception as e:  # noqa: BLE001
        problems.append(f"time zones: {e}")
    if not core.find_deno():
        problems.append("deno is not bundled")
    problems += transcribe.selftest()
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("\n".join(problems) if problems else "ok")
    return 1 if problems else 0


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        sys.exit(selftest(sys.argv[2]))
    App().mainloop()


if __name__ == "__main__":
    main()
