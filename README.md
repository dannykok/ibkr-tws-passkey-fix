# IBKR TWS passkey fix

A temporary workaround for phone-passkey authentication failures in IBKR Trader
Workstation (TWS) on Linux.

> **Disclaimer:** This is not an official IBKR solution and has neither been
> verified nor recommended by IBKR. Use it at your own risk.

## Who this repo is for

This workaround is for Linux users whose TWS phone-passkey login stalls after
scanning the QR code, with an “unable to connect” message on their phone.

Tested successfully on **Arch Linux (rolling release), x86-64**, with **TWS
launcher build `10511b` (10.51 series)** and the replacement runtime below.
Other TWS builds and Linux distributions have not been verified.

## The problem

The affected TWS installation bundles **JxBrowser 8.9.4 / Chromium 138**. Its
embedded passkey flow displays a QR code but can stall and end with a WebAuthn
`NotAllowedError`. The same phone passkey succeeds in a normal browser on the
same Linux machine.

### Suspected underlying cause

Phone-passkey login uses **hybrid authentication (caBLE v2)**. Scanning the QR
code is only the first step: Chromium must also recognize the phone's Bluetooth
Low Energy advertisement before establishing the authentication tunnel.

Chromium 138's older discovery helper caches device observations. Its duplicate
check compares legacy caBLE discovery data and service UUIDs, **not the complete
caBLE v2 advertisement payload**. If BlueZ initially reports the phone without
service data, then supplies the 20-byte payload later while the UUIDs remain
unchanged, Chromium can incorrectly discard that update as a duplicate. The
valid advertisement never reaches QR-session matching. [1]

This fits the observed failure: diagnostic logs recorded `Service data: <none>`
for a FIDO-advertising device, followed by a timeout. The resulting
`NotAllowedError` is therefore not necessarily a rejected or invalid passkey.

Chromium 147 uses a newer discovery path that reads the full v2 payload on device
updates and deduplicates the advertisement bytes themselves. Replacing TWS's
embedded runtime with that version produced successful logins. [2]

**This remains a suspected explanation, not a conclusively proven root cause in
the JxBrowser build shipped by IBKR.**

## The fix

Replace these three JARs together with official **JxBrowser 8.18.3 / Chromium 147**:

- `jxbrowser` — Java browser API
- `jxbrowser-swing` — Swing integration
- `jxbrowser-linux64` — bundled Linux Chromium

The patcher verifies pinned SHA-256 hashes, preserves TWS's expected filenames,
backs up the existing JARs, and restores them if a replacement fails midway.
An already-patched installation is left unchanged.

### Prerequisites and use

- Linux x86-64 and Python 3.10+; no third-party Python packages.
- An existing dedicated TWS copy at `~/Applications/TWS-Passkey/tws/`.
- Write access to that copy; internet access only if the preserved JARs are missing.

**Close TWS first**, then run:

```sh
cd ~/projects/ibkr-tws-passkey-fix
python3 ./patch-jxbrowser.py
```

By default, runtime files, backups, and the shared launch lock remain under
`~/Applications/TWS-Passkey/`, outside the updater-managed `tws/` directory.
Missing runtime JARs are downloaded from TeamDev's official Maven repository.
The original `~/Applications/Jts` installation is not the default target.

Then launch **Trader Workstation (Passkey)** normally. Optional commands:

```sh
python3 ./patch-jxbrowser.py --download-only
python3 ./patch-jxbrowser.py --work-dir /path/to/runtime-storage --tws-dir /path/to/TWS
```

## Agent skill

The agent local skill lives in `.agents/skills/tws-passkey-patch/SKILL.md`.

## Limitations

**This is not a permanent fix. TWS updates can overwrite the patched JARs, so
reapply the patch after updates that restore the affected runtime, until IBKR
fixes the bundled runtime in TWS itself.**

The substitution is unsupported by IBKR and pinned to the tested runtime, not
the latest release. Future TWS versions may require different APIs or a newer
runtime; do not blindly downgrade an official fixed bundle. There is no
automatic compatibility check, update monitoring, or reapplication. Successful
logins do not verify every TWS feature.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

Tests use temporary files and mocked downloads, not a real TWS session.
Runtime JARs, backups, account data, and credentials are not tracked in this repo.

## Sources

[1] https://raw.githubusercontent.com/chromium/chromium/138.0.7204.184/device/fido/cable/fido_cable_discovery.cc

[2] https://raw.githubusercontent.com/chromium/chromium/147.0.7727.56/device/fido/cable/v2_discovery.cc
