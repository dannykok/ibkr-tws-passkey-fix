# TWS passkey fix

## What this repo is for

A repeatable, manual workaround for phone-passkey login failures in native Linux
Interactive Brokers Trader Workstation (TWS). It includes a JxBrowser JAR patcher
and a repo-local agent skill, `tws-passkey-patch`.

## The problem

The affected TWS installation bundles **JxBrowser 8.9.4 / Chromium 138**. Its
embedded passkey flow displays a QR code but can stall and end with a WebAuthn
`NotAllowedError`. The same phone passkey succeeds in a normal browser on the
same Linux machine.

Replacing the embedded browser runtime produced successful TWS logins. A
Chromium Bluetooth/caBLE discovery issue is suspected, but the precise defect
has not been conclusively proven.

## The fix

Replace these three JARs together with official **JxBrowser 8.18.3 / Chromium 147**:

- `jxbrowser` — Java browser API
- `jxbrowser-swing` — Swing integration
- `jxbrowser-linux64` — bundled Linux Chromium

The patcher verifies pinned SHA-256 hashes, preserves TWS's expected filenames,
backs up the existing JARs, and restores them if a replacement fails midway.
An already-patched installation is left unchanged. Authentication, licensing,
settings, and Java font-antialiasing options are not modified.

### Prerequisites and use

- Linux x86-64 and Python 3.10+; no third-party Python packages.
- An existing dedicated TWS copy at `~/Applications/TWS-Passkey/tws/`.
- Write access to that copy; internet access only if the preserved JARs are missing.

**Close TWS first**, then run:

```sh
cd ~/projects/tws-passkey-fix
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

For agent-assisted patching, start Pi in this repo, grant project trust when
prompted, and invoke `/skill:tws-passkey-patch`. The local skill lives in
`.agents/skills/tws-passkey-patch/SKILL.md`.

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
