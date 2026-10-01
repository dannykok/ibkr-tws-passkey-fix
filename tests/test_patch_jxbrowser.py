import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "patch-jxbrowser.py"
SPEC = importlib.util.spec_from_file_location("replace_runtime", SCRIPT)
replace_runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replace_runtime)


class ReplaceRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.target = self.root / "tws"
        self.jars = self.target / "jars"
        self.jars.mkdir(parents=True)
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.payloads = {
            "jxbrowser": b"new core jar\n",
            "jxbrowser-swing": b"new swing jar\n",
            "jxbrowser-linux64": b"new linux jar\n",
        }
        self.original_artifacts = replace_runtime.ARTIFACTS
        replace_runtime.ARTIFACTS = {
            name: hashlib.sha256(payload).hexdigest() for name, payload in self.payloads.items()
        }

    def tearDown(self):
        replace_runtime.ARTIFACTS = self.original_artifacts
        self.temporary.cleanup()

    def make_destinations(self, versions=None):
        versions = versions or {
            "jxbrowser": "7.35.1",
            "jxbrowser-swing": "7.35.1",
            "jxbrowser-linux64": "7.35.1",
        }
        paths = {}
        for artifact in replace_runtime.ARTIFACTS:
            path = self.jars / f"{artifact}-{versions[artifact]}.jar"
            path.write_bytes(f"old {artifact}\n".encode())
            paths[artifact] = path
        return paths

    def make_runtime(self):
        directory = self.root / "runtime" / replace_runtime.VERSION
        directory.mkdir(parents=True)
        paths = {}
        for artifact, payload in self.payloads.items():
            path = directory / f"{artifact}-{replace_runtime.VERSION}.jar"
            path.write_bytes(payload)
            paths[artifact] = path
        return paths

    def fake_urlopen(self, changes=None, calls=None):
        payloads = dict(self.payloads)
        if changes:
            payloads.update(changes)

        def open_url(url, timeout=0):
            if calls is not None:
                calls.append((url, timeout))
            artifact = next(
                name for name in replace_runtime.ARTIFACTS if f"/{name}/{replace_runtime.VERSION}/" in url
            )
            return io.BytesIO(payloads[artifact])

        return open_url

    def run_execute(self, **kwargs):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            replace_runtime.execute(
                self.root,
                self.target,
                proc_root=self.proc,
                **kwargs,
            )
        return output.getvalue()

    def test_replacement_is_repeatable_and_idempotent(self):
        destinations = self.make_destinations()
        sources = self.make_runtime()
        os.chmod(destinations["jxbrowser"], 0o640)

        first_output = self.run_execute()
        backup_dirs = list((self.root / "backups").iterdir())
        self.assertEqual(1, len(backup_dirs))
        manifest = json.loads((backup_dirs[0] / "manifest.json").read_text())
        self.assertEqual(replace_runtime.VERSION, manifest["runtimeVersion"])
        self.assertEqual(
            {name: str(path) for name, path in destinations.items()}, manifest["targetPaths"]
        )
        self.assertEqual(replace_runtime.ARTIFACTS, manifest["sourceHashes"])
        for artifact, destination in destinations.items():
            self.assertEqual(self.payloads[artifact], destination.read_bytes())
            self.assertEqual(self.payloads[artifact], sources[artifact].read_bytes())
            self.assertTrue((backup_dirs[0] / destination.name).is_file())
        self.assertEqual(0o640, destinations["jxbrowser"].stat().st_mode & 0o777)
        self.assertIn("Applied JxBrowser", first_output)

        second_output = self.run_execute()
        self.assertIn("already applied", second_output)
        self.assertEqual(backup_dirs, list((self.root / "backups").iterdir()))

    def test_reapply_after_updater_overwrites_the_runtime(self):
        destinations = self.make_destinations()
        self.make_runtime()
        vmoptions = self.target / "tws.vmoptions"
        vmoptions.write_text("-Dawt.useSystemAAFontSettings=on\n-Dswing.aatext=true\n")
        self.run_execute()
        for artifact, path in destinations.items():
            path.write_bytes(f"updater's replacement {artifact}\n".encode())
        self.assertIn("Applied JxBrowser", self.run_execute())
        for artifact, path in destinations.items():
            self.assertEqual(path.read_bytes(), self.payloads[artifact])
        self.assertEqual(len(list((self.root / "backups").iterdir())), 2)
        self.assertEqual(vmoptions.read_text(), "-Dawt.useSystemAAFontSettings=on\n-Dswing.aatext=true\n")

    def test_default_cli_uses_existing_dedicated_work_dir(self):
        with mock.patch.object(replace_runtime, "execute") as execute:
            self.assertEqual(replace_runtime.main([]), 0)
        execute.assert_called_once_with(replace_runtime.DEFAULT_WORK_DIR, None, False)

    def test_cli_accepts_separate_target_and_work_dir(self):
        with mock.patch.object(replace_runtime, "execute") as execute:
            result = replace_runtime.main([
                "--work-dir", str(self.root), "--tws-dir", str(self.target),
            ])
        self.assertEqual(result, 0)
        execute.assert_called_once_with(self.root, self.target, False)

    def test_separate_work_dir_still_respects_launchers_shared_lock(self):
        destinations = self.make_destinations()
        storage = self.root / "separate-storage"
        storage.mkdir()
        originals = {p: p.read_bytes() for p in destinations.values()}
        with (self.root / ".runtime.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            with self.assertRaisesRegex(replace_runtime.ReplacementError, "lock is busy"):
                replace_runtime.execute(storage, self.target, proc_root=self.proc)
        for path, contents in originals.items():
            self.assertEqual(path.read_bytes(), contents)
        self.assertFalse((storage / "backups").exists())

    def test_separate_work_dir_patches_only_selected_target(self):
        destinations = self.make_destinations()
        storage = self.root / "separate-storage"
        unrelated = self.root / "original-installation.jar"
        unrelated.write_bytes(b"untouched")
        with contextlib.redirect_stdout(io.StringIO()):
            replace_runtime.execute(
                storage, self.target, proc_root=self.proc, urlopen=self.fake_urlopen(),
            )
        for artifact, path in destinations.items():
            self.assertEqual(path.read_bytes(), self.payloads[artifact])
        self.assertEqual(unrelated.read_bytes(), b"untouched")
        self.assertEqual(len(list((storage / "backups").iterdir())), 1)
        self.assertTrue((self.root / ".runtime.lock").exists())

    def test_changed_bundled_version_names_are_preserved(self):
        versions = {
            "jxbrowser": "8.0.0-beta.1",
            "jxbrowser-swing": "9.2.7",
            "jxbrowser-linux64": "10.0.0+vendor",
        }
        destinations = self.make_destinations(versions)
        old_names = {path.name for path in destinations.values()}
        self.make_runtime()

        self.run_execute()

        self.assertEqual(old_names, {path.name for path in self.jars.iterdir()})
        for artifact, destination in destinations.items():
            self.assertEqual(self.payloads[artifact], destination.read_bytes())

    def test_missing_or_ambiguous_layout_changes_no_jars(self):
        for case in ("missing", "ambiguous"):
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory(dir=self.root) as case_directory:
                    case_root = Path(case_directory)
                    target = case_root / "tws"
                    jars = target / "jars"
                    jars.mkdir(parents=True)
                    originals = {}
                    for artifact in replace_runtime.ARTIFACTS:
                        path = jars / f"{artifact}-7.1.jar"
                        path.write_bytes(artifact.encode())
                        originals[path] = path.read_bytes()
                    if case == "missing":
                        missing = jars / "jxbrowser-linux64-7.1.jar"
                        missing.unlink()
                        originals.pop(missing)
                    else:
                        extra = jars / "jxbrowser-9.0.jar"
                        extra.write_bytes(b"extra")
                        originals[extra] = b"extra"
                    proc = case_root / "proc"
                    proc.mkdir()
                    with self.assertRaisesRegex(replace_runtime.ReplacementError, "exactly one"):
                        replace_runtime.execute(case_root, target, proc_root=proc)
                    self.assertEqual(
                        {path: data for path, data in originals.items()},
                        {path: path.read_bytes() for path in originals},
                    )
                    self.assertFalse((case_root / "backups").exists())

    def test_existing_corrupt_runtime_is_not_replaced_or_used(self):
        destinations = self.make_destinations()
        runtime = self.make_runtime()
        runtime["jxbrowser-swing"].write_bytes(b"corrupt")
        calls = []
        originals = {path: path.read_bytes() for path in destinations.values()}

        with self.assertRaisesRegex(replace_runtime.ReplacementError, "checksum mismatch"):
            self.run_execute(urlopen=self.fake_urlopen(calls=calls))

        self.assertEqual([], calls)
        self.assertEqual(b"corrupt", runtime["jxbrowser-swing"].read_bytes())
        for path, data in originals.items():
            self.assertEqual(data, path.read_bytes())

    def test_bad_download_checksum_leaves_runtime_and_target_unchanged(self):
        destinations = self.make_destinations()
        originals = {path: path.read_bytes() for path in destinations.values()}

        with self.assertRaisesRegex(replace_runtime.ReplacementError, "Downloaded checksum mismatch"):
            self.run_execute(
                urlopen=self.fake_urlopen({"jxbrowser": b"not the pinned jar"})
            )

        runtime_dir = self.root / "runtime" / replace_runtime.VERSION
        self.assertEqual([], [path for path in runtime_dir.iterdir() if path.suffix == ".jar"])
        for path, data in originals.items():
            self.assertEqual(data, path.read_bytes())

    def test_download_only_restores_all_sources_without_tws_layout(self):
        shutil_target = self.target
        # Remove the target to establish that download-only never inspects or patches it.
        for path in self.jars.iterdir():
            path.unlink()
        self.jars.rmdir()
        shutil_target.rmdir()
        calls = []
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            replace_runtime.execute(
                self.root,
                download_only=True,
                proc_root=self.proc,
                urlopen=self.fake_urlopen(calls=calls),
            )
        self.assertEqual(3, len(calls))
        self.assertIn("is ready", output.getvalue())
        for artifact, payload in self.payloads.items():
            self.assertEqual(payload, replace_runtime.runtime_path(self.root, artifact).read_bytes())

    def test_rejects_symlinked_jars_directory_and_destination(self):
        external = self.root / "external"
        external.mkdir()
        external_file = external / "jxbrowser-7.1.jar"
        external_file.write_bytes(b"external")

        # A jars symlink must be rejected even when it points to a valid-looking directory.
        self.jars.rmdir()
        self.jars.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(replace_runtime.ReplacementError, "symbolic-link jars"):
            self.run_execute()
        self.assertEqual(b"external", external_file.read_bytes())

        self.jars.unlink()
        self.jars.mkdir()
        destinations = self.make_destinations()
        destinations["jxbrowser"].unlink()
        destinations["jxbrowser"].symlink_to(external_file)
        with self.assertRaisesRegex(replace_runtime.ReplacementError, "symbolic-link destination"):
            self.run_execute()
        self.assertEqual(b"external", external_file.read_bytes())

    def test_process_matching_obeys_path_boundaries_and_blocks_patch(self):
        target = self.target.resolve()
        self.assertTrue(
            replace_runtime.command_targets_directory(
                f"java\0-DinstallDir={target}\0".encode(), target
            )
        )
        self.assertTrue(
            replace_runtime.command_targets_directory(
                f"java\0{target}/jars/app.jar\0".encode(), target
            )
        )
        self.assertFalse(
            replace_runtime.command_targets_directory(
                f"java\0{target}-unrelated/jars/app.jar\0".encode(), target
            )
        )

        destinations = self.make_destinations()
        originals = {path: path.read_bytes() for path in destinations.values()}
        process = self.proc / "4242"
        process.mkdir()
        (process / "cmdline").write_bytes(f"java\0{target}/jars/app.jar\0".encode())
        with self.assertRaisesRegex(replace_runtime.ReplacementError, "PID 4242"):
            self.run_execute()
        for path, data in originals.items():
            self.assertEqual(data, path.read_bytes())

    def test_exclusive_lock_conflict_fails_without_changes(self):
        destinations = self.make_destinations()
        originals = {path: path.read_bytes() for path in destinations.values()}
        lock_path = self.root / ".runtime.lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with self.assertRaisesRegex(replace_runtime.ReplacementError, "lock is busy"):
                self.run_execute()
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        for path, data in originals.items():
            self.assertEqual(data, path.read_bytes())

    def test_target_directory_symlink_is_rejected(self):
        external = self.root / "external-tws"
        self.target.rename(external)
        self.target.symlink_to(external, target_is_directory=True)
        marker = external / "jars/marker"
        marker.write_bytes(b"unchanged")
        with self.assertRaisesRegex(replace_runtime.ReplacementError, "symbolic-link TWS"):
            self.run_execute()
        self.assertEqual(marker.read_bytes(), b"unchanged")

    def test_failed_download_publication_removes_temporary_files(self):
        destinations = self.make_destinations()
        originals = {p: p.read_bytes() for p in destinations.values()}
        with mock.patch.object(replace_runtime.os, "replace", side_effect=OSError("publish failed")):
            with self.assertRaisesRegex(OSError, "publish failed"):
                self.run_execute(urlopen=self.fake_urlopen())
        directory = self.root / "runtime" / replace_runtime.VERSION
        self.assertEqual(list(directory.iterdir()), [])
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)

    def _check_partial_commit_rollback(self, failure):
        destinations = self.make_destinations()
        self.make_runtime()
        originals = {path: path.read_bytes() for path in destinations.values()}
        actual_replace = os.replace
        failed = False

        def fail_second_commit(source, destination):
            nonlocal failed
            destination = Path(destination)
            if destination == destinations["jxbrowser-swing"] and not failed:
                failed = True
                raise failure
            return actual_replace(source, destination)

        with mock.patch.object(replace_runtime.os, "replace", side_effect=fail_second_commit):
            with self.assertRaisesRegex(
                replace_runtime.ReplacementError, "Already committed destinations were restored"
            ):
                self.run_execute()

        self.assertTrue(failed)
        for path, data in originals.items():
            self.assertEqual(data, path.read_bytes())
        backups = list((self.root / "backups").iterdir())
        self.assertEqual(1, len(backups))
        self.assertTrue((backups[0] / "manifest.json").is_file())
        self.assertEqual([], list(self.jars.glob(".*.tmp")))

    def test_partial_commit_is_rolled_back(self):
        self._check_partial_commit_rollback(OSError("simulated second commit failure"))

    def test_interrupted_commit_is_rolled_back(self):
        self._check_partial_commit_rollback(KeyboardInterrupt())


if __name__ == "__main__":
    unittest.main()
