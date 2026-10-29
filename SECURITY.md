# Security

## Reporting a problem

Found a security problem? Please use this repository's **Security** tab → **Report a vulnerability**,
or email the address in the README's **Getting help** section. Please don't post security problems in a
public issue, and never include API keys, passwords or personal details.

## What this app does and doesn't do

- It never asks for or uses a YouTube/Google login, cookies or browser data.
- It connects only to YouTube (logged out) and Google's Gemini API, using the user's own Gemini key.
- The Gemini key is stored encrypted with Windows DPAPI when Windows allows it.
- Each run's activity log is saved only on the user's PC (last 10 runs), with the API key removed.
- There is no telemetry. Once a day it asks GitHub for the newest version number and shows a link if there's
  a newer one (can be turned off in Settings → App). It never downloads or installs anything by itself.

Details are in the **Security** section of [README.md](README.md).

## Downloading safely

Only download releases from this repository's **Releases** page. Each release includes a SHA-256 checksum file
and a build attestation proving the file was built by GitHub from the public source code.
Each release has two checksums (one for the zip, one for `StreamTimestamper.exe`) so either file can be checked
or looked up on VirusTotal by hash. The exe carries its product name and version in Properties → Details.

Antivirus engines sometimes flag unsigned, PyInstaller-packaged apps with generic labels. If a scan flags the
file, compare the hashes, and report it to the address in the README. False alarms are reported to the
antivirus vendor. The app contains no code that edits the registry, adds itself to startup, or runs shell commands.
The author will never send the program by email attachment and will never ask you to turn off antivirus.
