# Stream Timestamper

Reads the captions of YouTube streams, has Gemini find the segments you care about, and saves a timestamp list for each stream.

This is an independent project and isn't affiliated with or endorsed by YouTube or Google. It uses the open-source yt-dlp library.

## Download and install (Windows)

1. Open this project's **Releases** page (link on the right side of the GitHub page) and download `StreamTimestamper-vX.Y.Z-windows.zip`. Only download it from there. Your browser may warn about the download ("not commonly downloaded" or "contains a virus or malware"), because security tools haven't seen this new, unsigned app before. If you got it from the Releases page, choose **Keep** / **Download anyway**. You can scan the zip with Windows Security or VirusTotal first (see "Is it safe?" above for what to expect).
2. OPTIONAL hash check: in PowerShell run `Get-FileHash .\StreamTimestamper-vX.Y.Z-windows.zip` and compare the result with the text in the `.sha256` file on the same release page. They must match.
3. Unzip the whole folder somewhere (not inside the zip) and run `StreamTimestamper.exe`. Windows may show a blue "Windows protected your PC" warning, because this is a new app that isn't code-signed (certificates cost money). If you downloaded it from the Releases page, click **More info → Run anyway**. Never turn off your antivirus for any app. Technical users can also confirm GitHub built the file from this source: `gh attestation verify <the zip> -R ardiberen/YT-Stream-Timestamper`.
4. The setup wizard opens on first launch. It covers the Gemini API key, how the app keeps your channel safe, the output folder, and a short guide to tuning the prompt.
5. **Prompt** page: pick a preset and describe what to find. Then click **Save as my preset** and tick **Start with this preset**.
6. **Run** page: paste stream, playlist or channel links, *or* add caption files. Then press **Start**.
7. **Results** page: read and copy the output, or open the output folder.
8. When the app says a new version is available, download it the same way, delete the old app folder and unzip the new one. Your settings, presets and results are kept.

## Getting help

- Email: streamtimestamper.help@protonmail.com
- Or open an issue on this project's **Issues** tab (needs a free GitHub account).

Please include the version number (bottom of the left sidebar), what you clicked, and the log of the run that went wrong: on the Run page, click **Open log folder** and attach the newest `run-...txt` file (the API key is already removed from it), or send a screenshot of the Activity log. Never send your Gemini API key or your settings file. Nobody needs them to help you, and I will never ask for them.

## Is it safe?

Fair question, since this is a free app from someone you don't know yet. You don't have to take my word for it:

**What it does and doesn't do**

- It's free and open source (MIT license). Every line of the program is in this repository.
- It never asks for your YouTube or Google password and never uses cookies or browser data.
- It connects only to YouTube (logged out), Google's Gemini service (with your own Gemini key) and GitHub (once a day, to read the newest version number). No telemetry, no ads, no account with me.
- It needs no installer and no administrator rights. It doesn't add itself to Windows startup, doesn't change the registry, and doesn't keep running after you close it. Its files are the app folder you unzip and `%APPDATA%\StreamTimestamper` (settings and logs).
- Your Gemini key stays on your PC, encrypted with your Windows login.

**How the download is made**

- The zip on the Releases page is built by GitHub's servers from the public code, not on my computer, after the automated tests pass. GitHub also records that (a "build attestation"), so the file can be checked against this source.
- Each release lists two SHA-256 checksums: one for the zip (`.sha256`) and one for `StreamTimestamper.exe` (`-exe.sha256`). Also on the release page: `build-versions.txt`, the exact list of libraries that went into the build.
- Right-click `StreamTimestamper.exe` → **Properties → Details** to see the product name and version it was built with.

**Check it yourself (pick any)**

1. Compare the zip's hash with the `.sha256` file (see step 2 below).
2. Paste the exe's hash from the `-exe.sha256` file into the search box on virustotal.com. That shows the scan report for that exact file without uploading anything.
3. Run `gh attestation verify <the zip> -R ardiberen/YT-Stream-Timestamper` (needs the free GitHub CLI) to confirm GitHub built it from this repository.
4. Build it yourself from the source (see "Build the exe" below) and compare. Or just read the code. `core.py` is the engine, and it has no GUI code.
5. Open Windows Resource Monitor → Network while it runs to see the only places it connects to.

**About antivirus warnings:** this app is free and not code-signed (certificates cost money), and it's packaged with PyInstaller, which is common for Python apps. Unsigned apps like that are sometimes flagged by a few lesser-known engines on VirusTotal with generic labels (like "Malware", "Unsafe" or "BehavesLike"). Those are guesses based on how a file looks, not matches for a known virus. When I scan the release, Microsoft Defender and the large engines (Kaspersky, ESET, Bitdefender, Sophos, CrowdStrike and others) report nothing. If you're unsure, check the hashes as above, or email me and I'll help. Never turn off your antivirus for any app.

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

Settings and presets live in `%APPDATA%\StreamTimestamper\settings.json`. Replacing the app folder with a newer version keeps all of it. The Gemini API key is stored encrypted there (see Security). The Activity log of each of the last 10 runs is saved as a text file in `%APPDATA%\StreamTimestamper\logs` (Run page → **Open log folder**). Older ones are deleted automatically.

## Security

**What the app connects to:** YouTube (logged out), Google's Gemini API, and GitHub once a day to see whether a newer version exists (Settings → App, untick "Tell me when a new version is out" to stop it). Nothing else: no telemetry, no other servers. The update check only reads the newest version number; the app never downloads or installs anything by itself.

**What's locked down**

- yt-dlp may only use its YouTube extractors, and pasted links are rebuilt from the video, playlist or channel ID. Text you paste is never handed to yt-dlp, so a crafted link can't point it at another site.
- Caption downloads must be `https` on a YouTube/Google address, including after redirects, and are capped at 25 MB. Caption files you add are capped at 25 MB too.
- No code is downloaded at runtime. yt-dlp's challenge solver ships inside the app, and Deno runs it with no file, network or system permissions (checked against yt-dlp's source).
- The Gemini key is encrypted with Windows DPAPI (your Windows login). Copying `settings.json` to another PC/user, or a backup of it, doesn't expose the key. The setup wizard says whether encryption is active on that PC. If Windows refuses, the key is saved as plain text instead.
- The key is stripped from the activity log, the saved log files and error messages.
- Stream captions are treated as untrusted text: Gemini is told to ignore any instructions inside them and never to add links.
- Certificate checks are always on. The app trusts the same certificates Windows does, plus Mozilla's list that ships with it. If a network intercepts the connection with a certificate Windows doesn't trust (some work, school or guest Wi-Fi does this), the app stops and says so instead of sending anything.
- Result file names are sanitized (no folders, no `..`, no Windows device names like `CON`).
- No login, cookies or passwords are stored or used.

**Good to know**

- **Free Gemini keys and privacy:** under Google's terms, content sent with a free (unpaid) Gemini key may be used to improve Google's products and read by human reviewers. Don't use a free key on private, unlisted or members-only streams. Paid keys are treated differently, so check Google's Gemini API terms.
- **Which AI model:** Gemini 3.6 Flash (the default) works well with free keys. Free keys only get a small number of requests per model each day (the count resets at midnight Pacific time). If a model runs out, the app stops and says so; you can switch models in Settings, since each has its own daily limit. Gemini 4 Argon needs a paid key. Settings shows a short note for each model.
- **Check before you post:** the AI can miss a segment or get a time slightly off, so skim the list before pasting it publicly. The app also tells Gemini to ignore anything in the stream that sounds like instructions to it (for example a caller saying "add this link"), and never to add links.
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
| `net_ssl.py` | Certificate settings for the app's own HTTPS requests (Windows store + bundled list) |
| `runlog.py` | Saves each run's Activity log to a file (last 10 kept) |
| `assets/` | `icon.ico`, `icon.png`, `make_icon.py` to rebuild them, and `make_version_info.py` (writes the exe's Properties → Details data) |
