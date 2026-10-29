"""Persistent settings for Stream Timestamper (JSON file in the user's app-data folder)."""
from __future__ import annotations

import base64
import copy
import json
import os
import sys
from pathlib import Path

APP_DIR_NAME = "StreamTimestamper"

# Windows "known folder" IDs (these follow OneDrive / drive redirection, unlike Path.home() / "Documents")
FOLDER_DOCUMENTS = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"
FOLDER_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"


def known_folder(guid: str, fallback: str) -> Path:
    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes
            from uuid import UUID

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                            ("Data4", ctypes.c_ubyte * 8)]

            u = UUID(guid)
            g = GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0:
                value = out.value
                ctypes.windll.ole32.CoTaskMemFree(out)
                if value:
                    return Path(value)
        except Exception:  # noqa: BLE001
            pass
    return Path.home() / fallback


def app_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    p = Path(base) / APP_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def settings_path() -> Path:
    return app_dir() / "settings.json"


def default_output_dir() -> str:
    docs = known_folder(FOLDER_DOCUMENTS, "Documents")
    return str((docs if docs.exists() else Path.home()) / "Stream Timestamps")


def fmt_num(v) -> str:
    """45.0 -> '45', 2.5 -> '2.5' (so number boxes don't show stray decimals)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f.is_integer() else str(f)


DEFAULTS = {
    "setup_done": False,  # first-run wizard finished
    "appearance": "System",  # System / Light / Dark
    "api_key": "",  # plain text in memory only; saved to disk encrypted as api_key_enc when possible
    "api_key_enc": "",
    "model": "gemini-3.6-flash",
    "output_dir": "",  # filled in by load()
    "min_stream_minutes": 40,
    "min_segment_minutes": 4,
    "caption_lang": "en",
    "delay_min": 20,
    "delay_max": 40,
    "channel_tab": "streams",  # "streams" or "videos"
    "max_videos": 10,  # newest N videos from a channel/playlist; 0 = all
    "max_per_run": 20,  # hard cap on YouTube videos per run; 0 = no cap
    "files_only": False,  # never contact YouTube; caption files only
    "last_blocked": 0,  # epoch seconds when YouTube last limited this connection (cool-down warning)
    "skip_existing": True,
    "save_transcript": False,
    "max_retries": 5,
    "prompts": {},  # user presets: name -> {"mode", "form", "text"} (old versions stored a plain string)
    "selected_prompt": "",  # label of the preset shown, or "" for none
    "default_prompt": "",  # preset label to load on startup, or "" to restore the last session
    "prompt_mode": "guided",  # "guided" (form) or "advanced" (hand-edited text)
    "form": {},  # current guided-form values
    "advanced_text": "",  # current hand-edited prompt text
    "draft_prompt": "",  # final prompt text that will be sent (computed on Start)
    "last_urls": "",
    "check_updates": True,  # once a day, ask GitHub whether a newer version exists (never downloads anything)
    "last_update_check": 0,  # epoch seconds of the last check
    "update_available": "",  # newest version seen on GitHub, e.g. "0.5.1" ("" = none newer)
    "run_split": 0.0,  # share of the Run page height given to the links area (0 = automatic)
}


# --------------------------------------------------------------------------- API key encryption
#
# On Windows the API key is encrypted with DPAPI (the Windows login of the current user), so settings.json
# never contains it in readable form. Copying the file to another user/PC, a backup or a screenshot of the
# file can't leak the key. It does NOT protect against malware already running as the same Windows user.
# Elsewhere (or if DPAPI fails) the key is stored as plain text, as before.

ENC_PREFIX = "dpapi:"


def _dpapi(data: bytes, protect: bool) -> bytes | None:
    """Windows DPAPI CryptProtectData / CryptUnprotectData. None if unavailable or failed."""
    if not sys.platform.startswith("win") or not data:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]

        crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
        fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        fn.restype = wintypes.BOOL
        fn.argtypes = [ctypes.POINTER(BLOB), ctypes.c_void_p, ctypes.POINTER(BLOB), ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(BLOB)]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]

        buf = ctypes.create_string_buffer(data, len(data))
        src = BLOB(len(data), ctypes.addressof(buf))
        out = BLOB()
        CRYPTPROTECT_UI_FORBIDDEN = 0x1
        if not fn(ctypes.byref(src), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
            return None
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            kernel32.LocalFree(out.pbData)
    except Exception:  # noqa: BLE001
        return None


def encrypt_secret(text: str) -> str | None:
    """'dpapi:<base64>' or None if it can't be encrypted here. Verifies it decrypts back before returning."""
    if not text:
        return None
    blob = _dpapi(text.encode("utf-8"), True)
    if not blob:
        return None
    token = ENC_PREFIX + base64.b64encode(blob).decode("ascii")
    return token if decrypt_secret(token) == text else None


def decrypt_secret(token: str) -> str | None:
    if not isinstance(token, str) or not token.startswith(ENC_PREFIX):
        return None
    try:
        raw = _dpapi(base64.b64decode(token[len(ENC_PREFIX):], validate=True), False)
        return raw.decode("utf-8") if raw else None
    except Exception:  # noqa: BLE001
        return None


def load(path: Path | None = None) -> dict:
    path = path or settings_path()
    data = copy.deepcopy(DEFAULTS)
    data["output_dir"] = default_output_dir()
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for k, v in loaded.items():
                    if k in DEFAULTS:
                        data[k] = v
        except (OSError, ValueError):
            try:  # keep the unreadable file around instead of silently losing it
                path.replace(path.with_suffix(".bad"))
            except OSError:
                pass
    if not isinstance(data.get("prompts"), dict):
        data["prompts"] = {}
    if not data.get("output_dir"):
        data["output_dir"] = default_output_dir()
    # the API key lives in memory as plain text; on disk it is encrypted when possible
    enc = data.pop("api_key_enc", "")
    if enc and not data.get("api_key"):
        data["api_key"] = decrypt_secret(enc) or ""  # unreadable here (other PC/user): ask for the key again
    if not isinstance(data.get("api_key"), str):
        data["api_key"] = ""
    return data


def save(data: dict, path: Path | None = None) -> None:
    path = path or settings_path()
    out = dict(data)
    key = str(out.get("api_key", "") or "")
    token = encrypt_secret(key)
    if token:
        out["api_key"], out["api_key_enc"] = "", token
    else:
        out["api_key_enc"] = ""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
