"""Turns the Prompt tab's guided form into the instruction text sent to Gemini. No GUI code."""
from __future__ import annotations

BUILTIN_PREFIX = "Built-in: "
SEPARATORS = ["–", "-", "|"]

DEFAULT_FORM = {
    "show": "",  # channel / show name (optional)
    "description": "",  # one-line description of the show (optional)
    "find": "",  # what counts as a segment worth listing (required)
    "ignore": "",  # what to skip (optional)
    "min_minutes": "4",  # minimum segment length
    "name_on": True,
    "detail_on": False,
    "detail_label": "Topic",
    "detail_examples": "",
    "length_on": True,
    "summary_on": False,
    "separator": "–",
    "header": "Timestamps:",  # blank = no header line
    "blank_between": True,
    "trailing_period": False,
    "chapters": False,  # add a 00:00 first line so YouTube makes chapters from a description
    "chapter_intro": "Intro",
}

BUILTIN_PRESETS: dict[str, dict] = {
    "Debate call-in show (callers who disagree with the host)": {
        "find": "a caller or guest argues against the host's position (for example religious callers debating an "
                "atheist host: Christians, Catholics, Muslims, theists, and so on)",
        "ignore": "the host, moderators, and callers who agree with the host",
        "min_minutes": "4",
        "detail_on": True,
        "detail_label": "Stance",
        "detail_examples": "e.g. Christian, Catholic, Muslim, theist",
        "trailing_period": True,
    },
    "Call-in show (every caller)": {
        "find": "a viewer calls in or joins the stream to talk with the host",
        "ignore": "the host and moderators while no caller is on, plus intros, ads and sponsor reads",
        "min_minutes": "3",
        "detail_on": True,
        "detail_label": "Topic",
        "detail_examples": "a few words on what they talk about",
    },
    "Interviews / guest segments": {
        "find": "the host interviews or has an extended conversation with a guest",
        "ignore": "intros, ads, sponsor reads and chat reading",
        "min_minutes": "5",
        "detail_on": True,
        "detail_label": "Topic",
        "detail_examples": "a few words on what they discuss",
        "summary_on": True,
    },
    "Viewer questions / Q&A": {
        "find": "the host reads and answers a question from a viewer, the chat, or a donation message",
        "ignore": "the host talking about other things between questions",
        "min_minutes": "1",
        "detail_on": True,
        "detail_label": "Question",
        "detail_examples": "a short paraphrase of the question",
    },
    "Blank (start from scratch)": {},
}


def normalize_form(d: dict | None) -> dict:
    out = dict(DEFAULT_FORM)
    for k, v in (d or {}).items():
        if k in DEFAULT_FORM:
            out[k] = v
    return out


def builtin_form(name: str) -> dict:
    return normalize_form(BUILTIN_PRESETS[name])


def _min(form: dict) -> float:
    try:
        return float(str(form.get("min_minutes", "0")).strip() or 0)
    except ValueError:
        return 0.0


def validate_form(form: dict) -> list[str]:
    errs = []
    if not str(form.get("find", "")).strip():
        errs.append("Fill in 'What should it find?' on the Prompt tab.")
    if not form.get("name_on") and not form.get("detail_on") and not form.get("length_on") and not form.get("summary_on"):
        pass  # start time alone is still a valid output
    try:
        if float(str(form.get("min_minutes", "0")).strip() or 0) < 0:
            errs.append("Minimum length can't be negative.")
    except ValueError:
        errs.append("Minimum length must be a number.")
    return errs


def build_prompt(form: dict) -> str:
    f = normalize_form(form)
    show, desc = f["show"].strip(), f["description"].strip()
    sep = f" {f['separator']} "
    use_min = _min(f) > 0

    who = f'the YouTube channel "{show}"' if show else "a YouTube live stream"
    first = f"You are an expert transcript analyzer for {who}"
    first += f" ({desc.rstrip('.')})." if desc else "."

    lines = [
        first,
        "The transcript has one line per caption in the form [HH:MM:SS] text. "
        "Speakers are not labeled, so work out who is talking from context.",
        "",
        "Task:",
    ]
    n = 1
    lines.append(f"{n}. Identify EVERY segment where {f['find'].strip().rstrip('.')}.")
    n += 1
    if f["ignore"].strip():
        lines.append(f"{n}. IGNORE {f['ignore'].strip().rstrip('.')}.")
        n += 1
    if use_min:
        lines.append(f"{n}. EXCLUDE any segment lasting LESS THAN {{min_minutes}} minutes.")
        n += 1
    lines.append(f"{n}. For each qualifying segment, output:")
    fields = ["Start timestamp (HH:MM:SS)"]
    row = ["HH:MM:SS"]
    if f["name_on"]:
        fields.append("Name (or handle/identifier)")
        row.append("Name")
    if f["detail_on"]:
        label = f["detail_label"].strip() or "Detail"
        ex = f["detail_examples"].strip()
        fields.append(f"{label}" + (f" ({ex})" if ex else ""))
        row.append(label)
    if f["length_on"]:
        fields.append("Approximate length (~X minutes)")
        row.append("Length: ~X minutes")
    if f["summary_on"]:
        fields.append("One-line summary")
        row.append("One-line summary")
    lines += [f"   - {x}" for x in fields]

    header = f["header"].strip()
    lines += ["", "Output format required:"]
    if header:
        lines += [header, ""]
    intro = f["chapter_intro"].strip() or "Intro"
    if f["chapters"]:
        lines.append(f"00:00{sep}{intro}")
        if f["blank_between"]:
            lines.append("")
    lines.append(sep.join(row))
    lines.append("")
    lines.append("(blank line between each entry)" if f["blank_between"] else "(one entry per line, no blank lines)")
    if f["trailing_period"]:
        lines += ["", "At the very end (two blank lines after the final entry), place a single period ('.')."]

    lines += ["", "Rules:"]
    lines.append("- List entries in time order, earliest first.")
    if f["chapters"]:
        lines.append(f'- The very first entry must be the line "00:00{sep}{intro}" exactly, even if nothing '
                     "qualifies until later. Every timestamp must be at least 10 seconds after the previous one.")
    if use_min:
        lines.append("- Exclude segments under {min_minutes} minutes.")
    start = f' starting with "{header}"' if header else ""
    if f["trailing_period"]:
        lines.append(f"- Output ONLY the formatted text{start} and ending with the period.")
    else:
        lines.append(f"- Output ONLY the formatted text{start}, with no other commentary.")
    lines.append("- If there are no qualifying segments, output only: No qualifying segments found.")
    return "\n".join(lines) + "\n"
