# Security

## Reporting a problem

Found a security problem? Please use this repository's **Security** tab → **Report a vulnerability**,
or email the address in the README's **Getting help** section. Please don't post security problems in a
public issue, and never include API keys, passwords or personal details.

## What this app does and doesn't do

- It never asks for or uses a YouTube/Google login, cookies or browser data.
- It connects only to YouTube (logged out) and Google's Gemini API, using the user's own Gemini key.
- The Gemini key is stored encrypted with Windows DPAPI when Windows allows it.
- There is no telemetry. Once a day it asks GitHub for the newest version number and shows a link if there's
  a newer one (can be turned off in Settings → App). It never downloads or installs anything by itself.

Details are in the **Security** section of [README.md](README.md).

## Downloading safely

Only download releases from this repository's **Releases** page. Each release includes a SHA-256 checksum file
and a build attestation proving the file was built by GitHub from the public source code.
The author will never send the program by email attachment and will never ask you to turn off antivirus.
