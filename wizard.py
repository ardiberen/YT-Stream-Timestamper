"""First-run setup wizard (also reused for "Change API key")."""
from __future__ import annotations

import os
import threading
from tkinter import filedialog, messagebox

import customtkinter as ctk

import core
import settings as store
import ui_common as ui

AI_STUDIO_URL = "https://aistudio.google.com/apikey"
YT_STUDIO_URL = "https://studio.youtube.com"

STEPS = [
    ("welcome", "Welcome"),
    ("gemini", "Gemini API key"),
    ("safety", "Keeping your channel safe"),
    ("output", "Where to save"),
    ("tour", "Getting the output you want"),
]

STUDIO_STEPS = [
    "Open YouTube Studio, signed in to the channel that owns the stream.",
    "In the left menu, click Languages, then pick the stream from the list.",
    "Make sure a video language is set at the top of the page (for example English). If it says none, set one first.",
    "Click the pencil icon next to \"Video language\". A window opens for that language.",
    "In that window, find the \"Automatic subtitles\" row and click the ⋮ (Show subtitle options) button on it. "
    "Then click Download and pick .vtt, .srt or .sbv. Any of the three works.",
    "Optional: rename the file to the stream's title. The result file is named after it.",
    "Back in this app: Run page → 'Add caption files…' → pick the file(s) → Start.",
]
STUDIO_NOTES = (
    "Only people with access to the channel in YouTube Studio can do this. If you're making timestamps for "
    "someone else's channel, ask them (or their channel manager) to send you the file.\n"
    "Auto captions can take a while to show up after a stream ends. YouTube moves its menus around now and "
    "then, so the labels may be slightly different."
)


class CaptionFileHelp(ctk.CTkToplevel):
    """'How to get a caption file from YouTube Studio' window."""

    def __init__(self, parent, on_add=None):
        super().__init__(parent)
        self.title("Get a caption file from YouTube Studio")
        self.geometry("680x560")
        self.configure(fg_color=ui.CONTENT_BG)
        self.transient(parent)
        ui.apply_window_icon(self)
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(16, 0))
        body.grid_columnconfigure(0, weight=1)
        ui.label(body, "Caption files: the zero-risk way", 20, "bold").grid(row=0, column=0, sticky="w", padx=8)
        ui.label(body, "The app reads the file on your computer and never contacts YouTube for it.", 13,
                 color=ui.MUTED, wrap=600).grid(row=1, column=0, sticky="w", padx=8, pady=(2, 14))
        for i, text in enumerate(STUDIO_STEPS, 1):
            f = ctk.CTkFrame(body, fg_color="transparent")
            f.grid(row=i + 1, column=0, sticky="ew", padx=8, pady=6)
            f.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(f, text=str(i), width=28, height=28, corner_radius=14, fg_color=ui.ACCENT,
                         text_color="#ffffff", font=ui.font(13, "bold")).grid(row=0, column=0, sticky="nw",
                                                                             padx=(0, 12))
            ui.label(f, text, 14, wrap=540).grid(row=0, column=1, sticky="w")
        ui.hint(body, STUDIO_NOTES, wrap=600).grid(row=len(STUDIO_STEPS) + 2, column=0, sticky="w", padx=8,
                                                 pady=(12, 8))
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=24, pady=16)
        ui.button(foot, "Open YouTube Studio", lambda: ui.open_url(YT_STUDIO_URL), kind="secondary").pack(
            side="left")
        if on_add:
            ui.button(foot, "Add caption files…", lambda: (self.destroy(), on_add())).pack(side="right")
        ui.button(foot, "Close", self.destroy, kind="ghost", width=90).pack(side="right", padx=8)
        self.after(250, self.lift)


class SetupWizard(ctk.CTkToplevel):
    def __init__(self, app, only: str | None = None):
        super().__init__(app)
        self.app = app
        self.only = only
        self.steps = [s for s in STEPS if only is None or s[0] == only]
        self.idx = 0
        self._verified_key: str | None = None
        self._test_result = None
        self._testing = False

        title = "Set up " + ui.APP_NAME if only is None else self.steps[0][1]
        self.title(title)
        self.geometry("900x700")
        self.minsize(780, 580)
        self.configure(fg_color=ui.CONTENT_BG)
        self.transient(app)
        ui.apply_window_icon(self)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # step rail (full wizard only)
        self.rail_labels = []
        if len(self.steps) > 1:
            rail = ctk.CTkFrame(self, width=230, corner_radius=0, fg_color=ui.SIDEBAR_BG)
            rail.grid(row=0, column=0, sticky="nsw")
            rail.grid_propagate(False)
            ui.label(rail, "Setup", 18, "bold").grid(row=0, column=0, sticky="w", padx=22, pady=(24, 14))
            for i, (_, name) in enumerate(self.steps):
                lb = ui.label(rail, "", 14)
                lb.grid(row=i + 1, column=0, sticky="w", padx=22, pady=6)
                self.rail_labels.append(lb)

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)
        self.main = main

        head = ctk.CTkFrame(main, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=30, pady=(24, 6))
        self.step_lbl = ui.label(head, "", 12, color=ui.MUTED)
        self.step_lbl.pack(anchor="w")
        self.title_lbl = ui.label(head, "", 24, "bold")
        self.title_lbl.pack(anchor="w")

        self.body = None  # rebuilt for every step

        foot = ctk.CTkFrame(main, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=30, pady=(8, 22))
        later = "Finish later" if only is None else "Cancel"
        ui.button(foot, later, self._cancel, kind="ghost", width=120).pack(side="left")
        self.next_btn = ui.button(foot, "Next", self._next, width=150)
        self.next_btn.pack(side="right")
        self.back_btn = ui.button(foot, "Back", self._back, kind="secondary", width=100)
        self.back_btn.pack(side="right", padx=10)

        self._show()
        self.after(250, self._grab)

    # ------------------------------------------------------------------ plumbing
    def _grab(self):
        try:
            self.lift()
            self.focus_force()
            self.grab_set()
        except Exception:  # noqa: BLE001
            pass

    def _close(self):
        try:
            self.grab_release()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()
        self.app.on_wizard_closed()

    def _cancel(self):
        if self.only is None and not self.app.s.get("setup_done"):
            if not messagebox.askyesno(
                    ui.APP_NAME, "Finish setup later?\n\nThe app can't run until your Gemini API key is set. "
                                 "You can restart setup any time from Settings.", parent=self):
                return
        self._close()

    def _key(self) -> str:
        return self.steps[self.idx][0]

    def _new_body(self):
        if self.body is not None:
            self.body.destroy()
        self.body = ctk.CTkScrollableFrame(self.main, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=18)
        self.body.grid_columnconfigure(0, weight=1)
        self._row = 0

    def _add(self, widget, pady=(0, 12), sticky="ew", padx=12):
        widget.grid(row=self._row, column=0, sticky=sticky, padx=padx, pady=pady)
        self._row += 1
        return widget

    def _para(self, text, muted=False, size=14):
        return self._add(ui.label(self.body, text, size, color=ui.MUTED if muted else None, wrap=600))

    def _item(self, n, text, buttons=()):
        f = ctk.CTkFrame(self.body, fg_color="transparent")
        f.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(f, text=str(n), width=28, height=28, corner_radius=14, fg_color=ui.ACCENT,
                     text_color="#ffffff", font=ui.font(13, "bold")).grid(row=0, column=0, sticky="nw", padx=(0, 12))
        ui.label(f, text, 14, wrap=560).grid(row=0, column=1, sticky="w")
        if buttons:
            bf = ctk.CTkFrame(f, fg_color="transparent")
            bf.grid(row=1, column=1, sticky="w", pady=(6, 0))
            for txt, cmd in buttons:
                ui.button(bf, txt, cmd, kind="secondary", height=32).pack(side="left", padx=(0, 8))
        return self._add(f, pady=(0, 14))

    def _show(self):
        key, name = self.steps[self.idx]
        self._new_body()
        n = len(self.steps)
        self.step_lbl.configure(text=f"STEP {self.idx + 1} OF {n}" if n > 1 else "")
        self.title_lbl.configure(text=name)
        for i, lb in enumerate(self.rail_labels):
            done, cur = i < self.idx, i == self.idx
            lb.configure(text=("✓  " if done else f"{i + 1}   ") + self.steps[i][1],
                         text_color=ui.NAV_SELECTED_TEXT if cur else (ui.OK if done else ui.MUTED),
                         font=ui.font(14, "bold" if cur else "normal"))
        self.back_btn.configure(state="normal" if self.idx > 0 else "disabled")
        last = self.idx == n - 1
        self.next_btn.configure(text=("Finish" if self.only is None else "Done") if last else "Next")
        getattr(self, f"_step_{key}")()

    def _back(self):
        if self.idx > 0:
            self.idx -= 1
            self._show()

    def _advance(self):
        if self.idx < len(self.steps) - 1:
            self.idx += 1
            self._show()
        else:
            self._finish()

    def _next(self):
        if self._testing:
            return
        getattr(self, f"_next_{self._key()}", self._advance)()

    def _finish(self):
        if self.only is None:
            self.app.s["setup_done"] = True
        self.app.persist()
        self._close()
        if self.only is None:
            self.app.show_page("prompt")

    # ------------------------------------------------------------------ welcome
    def _step_welcome(self):
        self._para("This app reads the captions of YouTube streams and uses Google's Gemini AI to list the moments "
                   "you care about (for example, every caller and the time they start). Each stream's list is saved "
                   "as a text file you can paste into a comment or description.")
        self._para("Setup takes about 3 minutes. You'll need:", size=15)
        self._item(1, "A Gemini API key from Google (a password that lets this app use Gemini).")
        self._item(2, "A folder to save results in.")
        self._para("You will NOT need to give it your YouTube login. More on that in step 3.", size=14)
        self._para("You can come back to any of this later from Settings.", muted=True)

    # ------------------------------------------------------------------ gemini
    def _step_gemini(self):
        self._para("The app sends each stream's captions to Google's Gemini AI. For that it needs your own API key.")
        self._item(1, "Open Google AI Studio and sign in with any Google account.",
                   [("Open AI Studio", lambda: ui.open_url(AI_STUDIO_URL))])
        self._item(2, "Click 'Create API key' (accept the terms if asked), then click the copy button next to "
                      "the new key.")
        self._item(3, "Paste it here:")

        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.grid_columnconfigure(0, weight=1)
        self.key_entry = ctk.CTkEntry(row, textvariable=self.app.v_key, show="•", height=38, font=ui.font(14),
                                      placeholder_text="Paste your API key")
        self.key_entry.grid(row=0, column=0, sticky="ew")
        self._show_key = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(row, text="Show", variable=self._show_key, width=70,
                        command=lambda: self.key_entry.configure(show="" if self._show_key.get() else "•")).grid(
            row=0, column=1, padx=10)
        self._add(row, padx=(52, 12))

        mrow = ctk.CTkFrame(self.body, fg_color="transparent")
        ui.label(mrow, "AI model:", 13, color=ui.MUTED).pack(side="left")
        ctk.CTkComboBox(mrow, values=ui.MODELS, variable=self.app.v_model, width=220).pack(side="left", padx=8)
        ui.label(mrow, "(leave this unless you know you want another)", 12, color=ui.MUTED).pack(side="left")
        self._add(mrow, padx=(52, 12))

        self.key_status = self._add(ui.label(self.body, "", 13, wrap=560), padx=(52, 12))
        self._para("Google offers a free tier with usage limits. Check AI Studio for current limits and pricing. "
                   "The key is saved only on this computer"
                   + (", encrypted with your Windows login." if store.encrypt_secret("probe-key-0") else "."),
                   muted=True, size=12)
        self._para("Privacy: under Google's terms, content sent with a free (unpaid) key may be used to improve "
                   "Google's products and read by human reviewers. Don't use it for private, unlisted or "
                   "members-only streams. Paid (billing-enabled) keys are treated differently; check Google's "
                   "Gemini API terms.", muted=True, size=12)

    def _next_gemini(self):
        key, model = self.app.v_key.get().strip(), self.app.v_model.get().strip()
        if not key:
            self.key_status.configure(text="Paste your API key first.", text_color=ui.ERROR)
            return
        if key == self._verified_key:
            self.app.persist()
            self._advance()
            return
        self._testing = True
        self.next_btn.configure(state="disabled", text="Checking key...")
        self.key_status.configure(text="Checking your key with Google...", text_color=ui.MUTED)
        self._test_result = None

        def work():
            self._test_result = core.test_api_key(key, model)

        threading.Thread(target=work, daemon=True).start()
        self.after(200, lambda: self._wait_test(key))

    def _wait_test(self, key):
        if self._test_result is None:
            self.after(200, lambda: self._wait_test(key))
            return
        self._testing = False
        ok, msg = self._test_result
        last = self.idx == len(self.steps) - 1
        self.next_btn.configure(state="normal", text=("Done" if self.only else "Finish") if last else "Next")
        if ok:
            self._verified_key = key
            self.key_status.configure(text="Key works.", text_color=ui.OK)
            self.app.persist()
            self._advance()
            return
        self.key_status.configure(text=msg, text_color=ui.ERROR)
        if not msg.startswith("Gemini rejected") and "not found" not in msg:
            if messagebox.askyesno(ui.APP_NAME, f"Couldn't confirm the key:\n{msg}\n\nContinue anyway?", parent=self):
                self._verified_key = key
                self.app.persist()
                self._advance()

    # ------------------------------------------------------------------ safety
    def _step_safety(self):
        good = ctk.CTkFrame(self.body, fg_color=ui.CARD_BG, corner_radius=12, border_width=1,
                            border_color=ui.CARD_BORDER)
        ui.label(good, "This app never signs in to YouTube.", 16, "bold").pack(anchor="w", padx=16, pady=(14, 2))
        ui.label(good, "It doesn't ask for or use your YouTube/Google login, cookies, or browser, and it ignores any "
                       "yt-dlp settings on this PC. It reads public captions the way a logged-out visitor would, "
                       "so your account and channel are not part of those requests.", 14, wrap=580).pack(
            anchor="w", padx=16, pady=(0, 14))
        self._add(good, pady=(0, 16))

        self._para("Built-in guardrails:", size=15)
        self._item(1, "A 20–40 second pause between videos, and at most 20 videos per run (both in Settings).")
        self._item(2, "If YouTube ever shows a 'confirm you're not a bot' check or says 'too many requests', the "
                      "app stops right away instead of retrying.")
        self._item(3, "It never uses YouTube's auto-translated captions, which are a known trigger for "
                      "'too many requests'.")

        self._para("Good habits:", size=15)
        self._item("•", "Process streams after they end, not while you're live.")
        self._item("•", "If YouTube limits you, wait several hours (or until tomorrow) before trying again.")
        self._item("•", "For zero contact with YouTube at all, download the stream's caption file from YouTube "
                        "Studio and give it to the app instead of a link.",
                   [("How to get a caption file", lambda: CaptionFileHelp(self))])

    # ------------------------------------------------------------------ output folder
    def _step_output(self):
        self._para("Each finished stream is saved here as a .txt file named after the stream. "
                   "You can also read and copy them on the app's Results page.")
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(row, textvariable=self.app.v_out, height=38, font=ui.font(14)).grid(row=0, column=0, sticky="ew")
        ui.button(row, "Browse...", self._browse_out, kind="secondary", width=110).grid(row=0, column=1, padx=(10, 0))
        self._add(row)
        self.out_status = self._add(ui.label(self.body, "", 13, wrap=600))

    def _browse_out(self):
        d = filedialog.askdirectory(parent=self, title="Choose where to save results",
                                    initialdir=self.app.v_out.get() or None)
        if d:
            self.app.v_out.set(d)

    def _next_output(self):
        d = self.app.v_out.get().strip()
        if not d:
            self.out_status.configure(text="Choose a folder.", text_color=ui.ERROR)
            return
        problem = core.check_writable(d)
        if problem:
            self.out_status.configure(text=problem, text_color=ui.ERROR)
            return
        self.app.persist()
        self._advance()

    # ------------------------------------------------------------------ tour
    def _step_tour(self):
        self._para("You're set up. Here's how to dial in the output for your channel:")
        self._item(1, "Prompt page: pick the built-in preset closest to your show. Fill in 'What should it find?' "
                      "in plain words (who counts and who doesn't), set the minimum length, and tick which details "
                      "each line should show.")
        self._item(2, "Run page: paste ONE stream link and press Start. Try one stream before doing a batch.")
        self._item(3, "Results page: read what came back. If it's off, go back to the Prompt page and be more "
                      "specific, then run the same stream again (untick 'Skip streams that already have results' "
                      "on the Run page so it gets redone).")
        self._item(4, "Happy with it? On the Prompt page, click 'Save as my preset' and tick 'Start with this "
                      "preset' so it loads every time.")
        self._item(5, "Settings page: set 'Skip streams shorter than' and how many of a channel's newest streams "
                      "to process when you paste a channel link.")
        self._item(6, "If a stream can't be read from its link (age-restricted, members-only, or YouTube is "
                      "limiting you), use its caption file from YouTube Studio instead.")
        self._para("Clicking Finish takes you to the Prompt page.", muted=True)
