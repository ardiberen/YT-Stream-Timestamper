# Stream Timestamper

Reads the captions of YouTube streams, has Gemini find the segments you care about, and saves a timestamp list for each stream.

This is an independent project and isn't affiliated with or endorsed by YouTube or Google. It uses the open-source yt-dlp library.

## Download and install (Windows)

1. Open this project's **Releases** page (link on the right side of the GitHub page) and download `StreamTimestamper-vX.Y.Z-windows.zip`. Only download it from there. Your browser may warn about the download ("not commonly downloaded" or "contains a virus or malware"), because security tools haven't seen this new, unsigned app before. If you got it from the Releases page, choose **Keep** / **Download anyway**. You can scan the zip with Windows Security or VirusTotal first.
2. OPTIONAL hash check: in PowerShell run `Get-FileHash .\StreamTimestamper-vX.Y.Z-windows.zip` and compare the result with the text in the `.sha256` file on the same release page. They must match.
3. Unzip the whole folder somewhere (not inside the zip) and run `StreamTimestamper.exe`. Windows may show a blue "Windows protected your PC" warning, because this is a new app that isn't code-signed (certificates cost money). If you downloaded it from the Releases page, click **More info → Run anyway**. Never turn off your antivirus for any app. Technical users can also confirm GitHub built the file from this source: `gh attestation verify <the zip> -R <owner>/<repo>`.
4. The setup wizard opens on first launch. It covers the Gemini API key, how the app keeps your channel safe, the output folder, and a short guide to tuning the prompt.
5. **Prompt** page: pick a preset and describe what to find. Then click **Save as my preset** and tick **Start with this preset**.
6. **Run** page: paste stream, playlist or channel links, *or* add caption files. Then press **Start**.
7. **Results** page: read and copy the output, or open the output folder.
8. When the app says a new version is available, download it the same way, delete the old app folder and unzip the new one. Your settings, presets and results are kept.

## Getting help

- Email: streamtimestamper.help@protonmail.com
- Or open an issue on this project's **Issues** tab (needs a free GitHub account).

Please include the version number (bottom of the left sidebar), what you clicked, and a screenshot of the Activity log on the Run page. Never send your Gemini API key or your settings file. Nobody needs them to help you, and I will never ask for them.

## Account safety

The app **never signs in to YouTube**:

- It doesn't use any login, cookies or browser data.
- yt-dlp always runs with `--ignore-config --no-cookies --no-cookies-from-browser`, so a yt-dlp config file on the PC can't add a login either.
- Captions are read the way a logged-out visitor would read them.

Guardrails (in Settings → YouTube safety):

- It pauses 20–40 s between videos and processes at most 20 videos per run.
- It stops immediately on YouTube's "not a bot" check or a "429 Too Many Requests" error, with no retries.
- After a block, it warns you before contacting YouTube again for 6 hours.
- It never requests YouTube's machine-translated captions, which are a known 429 trigger.
- **Caption files only** mode makes the app never contact YouTube at all.

**Caption files** have zero contact with YouTube. In YouTube Studio: Languages (left menu) → pick the stream → make sure a language is set → click the pencil next to "Video language" → ⋮ next to Automatic subtitles → Download (.vtt, .srt or .sbv). Then on the Run page, use **Add caption files…**. This works only for people with Studio access to the channel.

Settings and presets live in `%APPDATA%\StreamTimestamper\settings.json`. Replacing the app folder with a newer version keeps all of it. The Gemini API key is stored encrypted there (see Security).

## Security

**What the app connects to:** YouTube (logged out), Google's Gemini API, and GitHub once a day to see whether a newer version exists (Settings → App, untick "Tell me when a new version is out" to stop it). Nothing else: no telemetry, no other servers. The update check only reads the newest version number; the app never downloads or installs anything by itself.

**What's locked down**

- yt-dlp may only use its YouTube extractors, and pasted links are rebuilt from the video, playlist or channel ID. Text you paste is never handed to yt-dlp, so a crafted link can't point it at another site.
- Caption downloads must be `https` on a YouTube/Google address, including after redirects, and are capped at 25 MB. Caption files you add are capped at 25 MB too.
- No code is downloaded at runtime. yt-dlp's challenge solver ships inside the app, and Deno runs it with no file, network or system permissions (checked against yt-dlp's source).
- The Gemini key is encrypted with Windows DPAPI (your Windows login). Copying `settings.json` to another PC/user, or a backup of it, doesn't expose the key. The setup wizard says whether encryption is active on that PC. If Windows refuses, the key is saved as plain text instead.
- The key is stripped from the activity log and error messages.
- Result file names are sanitized (no folders, no `..`, no Windows device names like `CON`).
- No login, cookies or passwords are stored or used.

**Good to know**

- **Free Gemini keys and privacy:** under Google's terms, content sent with a free (unpaid) Gemini key may be used to improve Google's products and read by human reviewers. Don't use a free key on private, unlisted or members-only streams. Paid keys are treated differently, so check Google's Gemini API terms.
- **Unsigned app:** the exe isn't code-signed (certificates cost money), so Windows or your antivirus may warn about it. The source is public and each release is built by GitHub from it.

## Build the exe (on Windows)

```
winget install DenoLand.Deno      (once; build.bat bundles deno.exe into the app)
build.bat
```

Output: `dist\StreamTimestamper\StreamTimestamper.exe`. Zip that whole folder to share it. Ignore the `build\` folder, which is PyInstaller scratch space.

Bump `APP_VERSION` in `version.py` for each build you hand out. To change the icon, run `python assets\make_icon.py your-image.png`.

## Run from source

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

## Tests

```
python -m unittest discover -s tests -v      (logic: no extra packages needed)
```

`tests/gui/README.md` explains the headless GUI smoke test.

## Files

| File | What it does |
|---|---|
| `app.py` | Main window: sidebar, Run, Prompt, Results and Settings pages |
| `wizard.py` | First-run setup wizard; caption-file how-to window |
| `ui_common.py` | Colors, fonts, card and button helpers, icon |
| `core.py` | Links (logged-out yt-dlp) or caption files → Gemini → file. No GUI |
| `prompt_builder.py` | Turns the Prompt page form into AI instructions; built-in presets |
| `settings.py` | settings.json load/save; encrypts the API key |
| `version.py` | App version (the GitHub build adds the repo name for update checks) |
| `updates.py` | Once-a-day "new version available" check against GitHub Releases |
| `assets/` | `icon.ico`, `icon.png`, and `make_icon.py` to rebuild them |
