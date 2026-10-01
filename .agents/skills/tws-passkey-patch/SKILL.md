---
name: tws-passkey-patch
description: Reapply the pinned JxBrowser JAR workaround for Linux IBKR Trader Workstation phone-passkey failures, especially after a TWS update overwrites the working embedded browser runtime. Use when asked to patch or restore the TWS passkey workaround.
compatibility: Linux x86-64, Python 3.10+, write access to the target TWS copy; internet access if runtime JARs are missing.
---

# Patch TWS's embedded passkey browser

Use the existing script; do not manually swap Chromium executables or individual
JARs. Resolve `../../../patch-jxbrowser.py` and `../../../README.md` relative to
this skill directory, not the shell's current working directory.

## Procedure

1. Read the repo README and confirm the intended installation. The default is
   `~/Applications/TWS-Passkey/tws/`, **not** `~/Applications/Jts`. Use another
   target only if the user explicitly identifies it. If the target is absent,
   ask for its location; this script does not install or clone TWS.
2. Check whether that TWS copy is running. The script refuses a busy launch lock
   or matching live process. If it is running, ask the user to close it unless
   they already authorized a restart. Do not forcibly kill a trading session.
3. Run the repo patcher with Python 3. For the default installation:

   ```sh
   python3 /resolved/repo/path/patch-jxbrowser.py
   ```

   Only for an explicitly selected alternative installation:

   ```sh
   python3 /resolved/repo/path/patch-jxbrowser.py \
     --work-dir /path/to/runtime-storage --tws-dir /path/to/TWS
   ```

   The default work directory is `~/Applications/TWS-Passkey/`. It holds the
   separately preserved runtime, backups, and the lock used by `launch-tws.sh`.
   `--download-only` restores missing runtime files without patching TWS.
4. Require exit status zero. The script checks all three pinned vendor hashes,
   validates the destination layout, backs up the current JARs, stages and
   replaces all three while keeping their filenames, and verifies the result.
   `already applied` means no replacement was needed. A checksum mismatch,
   ambiguous layout, or rollback error is a failure: report it and stop. Do not
   disable guards or overwrite corrupt sources to force success.
5. Report the target, runtime version, whether replacement was performed, and
   the printed backup directory when applicable. Do not claim a login or full
   compatibility test unless one was actually performed.
6. Relaunch only if requested, using the existing dedicated `launch-tws.sh`.
   Leave the phone-passkey authentication to the user.

## Boundaries

- The pinned runtime is JxBrowser 8.18.3 / Chromium 147, replacing the affected
  JxBrowser 8.9.4 / Chromium 138 bundle. The exact Chromium discovery defect
  remains suspected, not proven.
- Change only the three JxBrowser JARs. Preserve authentication, licensing,
  account settings, font options, Bluetooth configuration, and the original
  installation unless explicitly selected by the user.
- Do not commit vendor JARs, backups, TWS logs, settings, or credentials.
- This is a manual, unsupported workaround. Updates can undo it; repeat when
  needed until TWS ships an official fix. Do not automatically change the pinned
  version or downgrade a known-fixed/newer bundle.
