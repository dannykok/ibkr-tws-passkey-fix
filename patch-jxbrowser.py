#!/usr/bin/env python3
"""Manually replace Linux TWS's JxBrowser JARs with a pinned vendor runtime."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import sys
import tempfile
import urllib.request

VERSION = "8.18.3"
DEFAULT_WORK_DIR = Path.home() / "Applications" / "TWS-Passkey"
BASE_URL = "https://europe-maven.pkg.dev/jxbrowser/releases/com/teamdev/jxbrowser"
ARTIFACTS = {
    "jxbrowser": "f42a781ec921281d3428dc35be95154168677594070f75a7e61804f9691382b6",
    "jxbrowser-swing": "906beaa6506bb0148065ac90c71c5399cc10d0b652ee39f55b1c86df05986112",
    "jxbrowser-linux64": "05a659b0d4438e76117c0f1f6f4c6d9cb05d29b0db262e8bd208fc2398edac69",
}


class ReplacementError(Exception):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def exclusive_lock(root: Path):
    path = root / ".runtime.lock"
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise ReplacementError(f"Cannot open runtime lock {path}: {exc}") from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise ReplacementError(
                    f"Runtime lock is busy ({path}); TWS or another replacement may be running"
                ) from exc
            raise
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def command_targets_directory(cmdline: bytes, target: Path) -> bool:
    """Match target paths, while not matching similarly prefixed directories."""
    target_text = str(target)
    for raw in cmdline.split(b"\0"):
        if not raw:
            continue
        argument = raw.decode("utf-8", "surrogateescape")
        if argument == f"-DinstallDir={target_text}":
            return True
        # Covers plain arguments, option values, and Linux classpath entries.
        for component in re.split(r"[=:]", argument):
            if component == target_text or component.startswith(target_text + os.sep):
                return True
    return False


def reject_running_target(target: Path, proc_root: Path = Path("/proc")) -> None:
    try:
        processes = list(proc_root.iterdir())
    except OSError as exc:
        raise ReplacementError(f"Cannot inspect {proc_root} for a running TWS: {exc}") from exc
    for process in processes:
        if not process.name.isdigit() or process.name == str(os.getpid()):
            continue
        try:
            cmdline = (process / "cmdline").read_bytes()
        except OSError:  # Processes may exit, or be unreadable, during the scan.
            continue
        if command_targets_directory(cmdline, target):
            raise ReplacementError(
                f"Target TWS appears to be running as PID {process.name}; stop it first"
            )


def runtime_path(root: Path, artifact: str) -> Path:
    return root / "runtime" / VERSION / f"{artifact}-{VERSION}.jar"


def artifact_url(artifact: str) -> str:
    return f"{BASE_URL}/{artifact}/{VERSION}/{artifact}-{VERSION}.jar"


def ensure_runtime(root: Path, urlopen=None) -> dict[str, Path]:
    """Verify existing sources and download only sources that are absent."""
    opener = urlopen or urllib.request.urlopen
    directory = root / "runtime" / VERSION
    if (root / "runtime").is_symlink() or directory.is_symlink():
        raise ReplacementError("Runtime directory must not be a symbolic link")
    sources, missing = {}, []
    for artifact, expected in ARTIFACTS.items():
        source = runtime_path(root, artifact)
        sources[artifact] = source
        if source.is_symlink():
            raise ReplacementError(f"Runtime JAR must not be a symbolic link: {source}")
        if not source.exists():
            missing.append(artifact)
        elif not source.is_file():
            raise ReplacementError(f"Runtime JAR is not a regular file: {source}")
        elif sha256_file(source) != expected:
            raise ReplacementError(
                f"Runtime JAR checksum mismatch: {source}; refusing to replace or use it"
            )
    if not missing:
        return sources

    directory.mkdir(parents=True, exist_ok=True)
    staged = {}
    try:
        for artifact in missing:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{artifact}-", suffix=".download", dir=directory
            )
            temporary = Path(name)
            staged[artifact] = temporary
            digest = hashlib.sha256()
            try:
                with os.fdopen(descriptor, "wb") as output:
                    with opener(artifact_url(artifact), timeout=60) as response:
                        for chunk in iter(lambda: response.read(1024 * 1024), b""):
                            output.write(chunk)
                            digest.update(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(temporary, 0o644)
            except Exception as exc:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                if isinstance(exc, ReplacementError):
                    raise
                raise ReplacementError(
                    f"Download failed for {artifact} from {artifact_url(artifact)}: {exc}"
                ) from exc
            if digest.hexdigest() != ARTIFACTS[artifact]:
                raise ReplacementError(
                    f"Downloaded checksum mismatch for {artifact}; expected {ARTIFACTS[artifact]}"
                )
        # Every missing file is staged and verified before any enters runtime/.
        for artifact in missing:
            destination = sources[artifact]
            if destination.exists() or destination.is_symlink():
                raise ReplacementError(f"Runtime JAR appeared during download: {destination}")
            os.replace(staged[artifact], destination)
            del staged[artifact]
    finally:
        for temporary in staged.values():
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    return sources


def locate_destinations(target: Path):
    jars = target / "jars"
    if jars.is_symlink():
        raise ReplacementError(f"Refusing symbolic-link jars directory: {jars}")
    if not jars.is_dir():
        raise ReplacementError(f"TWS jars directory does not exist: {jars}")
    entries = list(jars.iterdir())
    destinations = {}
    for artifact in ARTIFACTS:
        pattern = re.compile(rf"{re.escape(artifact)}-[0-9][A-Za-z0-9._+\-]*\.jar\Z")
        matches = [path for path in entries if pattern.fullmatch(path.name)]
        if len(matches) != 1:
            raise ReplacementError(
                f"Expected exactly one {artifact}-<version>.jar in {jars}; found {len(matches)}"
            )
        destinations[artifact] = matches[0]

    hashes, modes = {}, {}
    for artifact, destination in destinations.items():
        if destination.is_symlink():
            raise ReplacementError(f"Refusing symbolic-link destination JAR: {destination}")
        metadata = destination.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode):
            raise ReplacementError(f"Destination JAR is not a regular file: {destination}")
        hashes[artifact] = sha256_file(destination)
        modes[artifact] = stat.S_IMODE(metadata.st_mode)
    return jars, destinations, hashes, modes


def stage_copy(source: Path, directory: Path, mode: int, label: str) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".jxbrowser-{label}-", suffix=".tmp", dir=directory
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_stream:
            shutil.copyfileobj(input_stream, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        return temporary
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def create_backup(root, target, destinations, destination_hashes):
    backup_root = root / "backups"
    if backup_root.is_symlink():
        raise ReplacementError(f"Refusing symbolic-link backups directory: {backup_root}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_dir = backup_root / f"{stamp}-{os.getpid()}-{secrets.token_hex(3)}"
    backup_dir.mkdir(parents=True)
    backups = {}
    for artifact, destination in destinations.items():
        backup = backup_dir / destination.name
        shutil.copy2(destination, backup, follow_symlinks=False)
        if sha256_file(backup) != destination_hashes[artifact]:
            raise ReplacementError(f"Backup validation failed for {destination}")
        backups[artifact] = backup
    manifest = {
        "runtimeVersion": VERSION,
        "targetDirectory": str(target),
        "targetPaths": {key: str(path) for key, path in destinations.items()},
        "sourceHashes": dict(ARTIFACTS),
        "originalHashes": destination_hashes,
        "backupFiles": {key: path.name for key, path in backups.items()},
    }
    with (backup_dir / "manifest.json").open("w", encoding="utf-8") as output:
        json.dump(manifest, output, indent=2, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    return backup_dir, backups


def replace_destinations(
    root, target, jars, sources, destinations, destination_hashes, modes
):
    backup_dir, backups = create_backup(root, target, destinations, destination_hashes)
    staged = {}
    try:
        for artifact in ARTIFACTS:
            staged[artifact] = stage_copy(sources[artifact], jars, modes[artifact], artifact)
            if sha256_file(staged[artifact]) != ARTIFACTS[artifact]:
                raise ReplacementError(f"Staged JAR checksum mismatch for {artifact}")
    except BaseException as exc:
        for path in staged.values():
            path.unlink(missing_ok=True)
        if isinstance(exc, ReplacementError) or not isinstance(exc, Exception):
            raise
        raise ReplacementError(f"Could not stage replacement JARs: {exc}") from exc

    committed = []
    try:
        for artifact in ARTIFACTS:
            # Record the attempt first so an interrupt immediately after rename
            # still restores this destination from its backup.
            committed.append(artifact)
            os.replace(staged[artifact], destinations[artifact])
            del staged[artifact]
        for artifact, destination in destinations.items():
            if sha256_file(destination) != ARTIFACTS[artifact]:
                raise ReplacementError(f"Committed JAR checksum mismatch for {artifact}")
    except BaseException as commit_error:
        rollback_errors = []
        for artifact in reversed(committed):
            restore = None
            try:
                restore = stage_copy(backups[artifact], jars, modes[artifact], "restore")
                os.replace(restore, destinations[artifact])
            except Exception as exc:
                rollback_errors.append(f"{artifact}: {exc}")
                if restore:
                    restore.unlink(missing_ok=True)
        cause = str(commit_error) or type(commit_error).__name__
        message = f"Replacement failed: {cause}. Backup: {backup_dir}."
        if rollback_errors:
            message += " Rollback was incomplete: " + "; ".join(rollback_errors)
        else:
            message += " Already committed destinations were restored."
        raise ReplacementError(message) from commit_error
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)
    return backup_dir


def execute(
    root: Path,
    tws_dir: Path | None = None,
    download_only: bool = False,
    proc_root: Path = Path("/proc"),
    urlopen=None,
) -> None:
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with ExitStack() as locks:
        locks.enter_context(exclusive_lock(root))
        if download_only:
            sources = ensure_runtime(root, urlopen)
            print(f"Runtime {VERSION} is ready at {next(iter(sources.values())).parent}")
            return
        requested_target = (tws_dir or root / "tws").expanduser()
        if requested_target.is_symlink():
            raise ReplacementError(f"Refusing symbolic-link TWS directory: {requested_target}")
        target = requested_target.resolve()
        print(f"Target TWS directory: {target}")
        # Coordinate with launch-tws.sh even when runtime storage is elsewhere.
        if target.parent != root:
            locks.enter_context(exclusive_lock(target.parent))
        reject_running_target(target, proc_root)
        jars, destinations, hashes, modes = locate_destinations(target)
        sources = ensure_runtime(root, urlopen)
        if all(hashes[artifact] == ARTIFACTS[artifact] for artifact in ARTIFACTS):
            print(f"JxBrowser {VERSION} is already applied; no files were changed")
            return
        backup = replace_destinations(
            root, target, jars, sources, destinations, hashes, modes
        )
        print(f"Applied JxBrowser {VERSION} to {target}")
        print(f"Original JARs backed up at {backup}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-dir", type=Path, default=DEFAULT_WORK_DIR,
        help=f"runtime, backups, and lock directory (default: {DEFAULT_WORK_DIR})",
    )
    parser.add_argument(
        "--download-only", action="store_true",
        help="only restore and verify the separately preserved pinned runtime",
    )
    parser.add_argument(
        "--tws-dir", type=Path,
        help="target copied TWS installation (default: <work-dir>/tws)",
    )
    arguments = parser.parse_args(argv)
    try:
        execute(arguments.work_dir, arguments.tws_dir, arguments.download_only)
    except (ReplacementError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
