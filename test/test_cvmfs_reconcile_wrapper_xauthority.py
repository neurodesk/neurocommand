import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "cvmfs" / "reconcile_wrapper_xauthority.py"

spec = importlib.util.spec_from_file_location("reconcile_wrapper_xauthority", SCRIPT)
reconcile = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = reconcile
spec.loader.exec_module(reconcile)


XAUTHORITY_BLOCK = """xauthority_opts=()
if [[ -n "${XAUTHORITY:-}" && -f "$XAUTHORITY" ]]; then
  xauthority_opts=(--bind "$XAUTHORITY:$XAUTHORITY:ro" --env "XAUTHORITY=$XAUTHORITY")
fi
"""


def legacy_wrapper(container_dir, command, bind_option=""):
    container_name = container_dir.name
    return (
        "#!/usr/bin/env bash\n"
        "export PWD=`pwd -P`\n"
        "singularity --silent exec --cleanenv --env DISPLAY=$DISPLAY "
        f"{bind_option} $neurodesk_singularity_opts --pwd \"$PWD\" "
        f"{container_dir}/{container_name}.simg {command} \"$@\"\n"
    )


def fixed_wrapper(container_dir, command, bind_option=""):
    legacy = legacy_wrapper(container_dir, command, bind_option)
    return legacy.replace(
        "export PWD=`pwd -P`\n",
        "export PWD=`pwd -P`\n" + XAUTHORITY_BLOCK,
        1,
    ).replace(
        "--env DISPLAY=$DISPLAY ",
        '--env DISPLAY=$DISPLAY "${xauthority_opts[@]}" ',
        1,
    )


def legacy_wrapper_without_display(container_dir, command, bind_option=""):
    container_name = container_dir.name
    return (
        "#!/usr/bin/env bash\n"
        "export PWD=`pwd -P`\n"
        f"singularity --silent exec {bind_option} $neurodesk_singularity_opts "
        f"--pwd \"$PWD\" {container_dir}/{container_name}.simg {command} \"$@\"\n"
    )


def disabled_wrapper(container_dir):
    image, builddate = container_dir.name.rsplit("_", 1)
    return (
        "#!/usr/bin/env bash\n"
        "echo \"This container was disabled due to a known bug or vulnerability. "
        "To keep using the software please use a different version. If you absolutely "
        "need this container for reproducibility you can pull it from docker hub via "
        f"the command apptainer pull docker://vnmd/{image}:{builddate}\"\n"
    )


def write_wrapper(container_dir, command, content, mode=0o755):
    wrapper = container_dir / command
    wrapper.write_text(content)
    wrapper.chmod(mode)
    return wrapper


def make_container(repo_root, commands):
    container = repo_root / "containers" / "demo_1.0_20260101"
    container.mkdir(parents=True)
    (container / "commands.txt").write_text("".join(f"{name}\n" for name in commands))
    return container


class WrapperReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.repo_root = Path(self.temporary_directory.name) / "neurodesk.ardc.edu.au"

    def test_applies_legacy_wrapper_in_place_and_converges(self):
        container = make_container(self.repo_root, ["demo"])
        wrapper = write_wrapper(container, "demo", legacy_wrapper(container, "demo"))
        before = wrapper.stat()

        plan = reconcile.plan_wrapper_reconciliation(self.repo_root)

        self.assertEqual(len(plan.rewrites), 1)
        self.assertEqual(plan.diagnostics, ())
        self.assertEqual(reconcile.apply_wrapper_plan(plan), 1)
        self.assertEqual(wrapper.read_text(), fixed_wrapper(container, "demo"))

        after = wrapper.stat()
        self.assertEqual(after.st_ino, before.st_ino)
        self.assertEqual(stat.S_IMODE(after.st_mode), stat.S_IMODE(before.st_mode))
        self.assertEqual(after.st_uid, before.st_uid)
        self.assertEqual(after.st_gid, before.st_gid)

        second_plan = reconcile.plan_wrapper_reconciliation(self.repo_root)
        self.assertTrue(second_plan.is_clean)
        self.assertEqual(reconcile.apply_wrapper_plan(second_plan), 0)

    def test_supports_every_generated_temporary_directory_bind_variant(self):
        variants = [
            legacy_wrapper(
                Path("/cvmfs/neurodesk.ardc.edu.au/containers/demo_1.0_20260101"),
                "demo",
                bind_option,
            )
            for bind_option in (
                "",
                "--bind $TMP:/tmp",
                "--bind $TMPDIR:/tmp",
                "--bind $TEMP:/tmp",
                "--bind $TEMPDIR:/tmp",
            )
        ]
        variants.append(variants[0].replace("DISPLAY=$DISPLAY  ", "DISPLAY=$DISPLAY ", 1))

        for legacy in variants:
            with self.subTest(invocation=legacy.splitlines()[-1]):
                with tempfile.TemporaryDirectory() as directory:
                    repo_root = Path(directory) / "repo"
                    container = make_container(repo_root, ["demo"])
                    live_path = Path(
                        "/cvmfs/neurodesk.ardc.edu.au/containers"
                    ) / container.name
                    wrapper = write_wrapper(
                        container,
                        "demo",
                        legacy.replace(str(live_path), str(container)),
                    )

                    plan = reconcile.plan_wrapper_reconciliation(repo_root)
                    self.assertEqual(len(plan.rewrites), 1)
                    reconcile.apply_wrapper_plan(plan)
                    wrapper_text = wrapper.read_text()
                    self.assertIn(XAUTHORITY_BLOCK, wrapper_text)
                    self.assertIn(
                        '--env DISPLAY=$DISPLAY "${xauthority_opts[@]}" ',
                        wrapper_text,
                    )

    def test_supports_older_wrappers_that_inherited_display(self):
        variants = [
            legacy_wrapper_without_display(
                Path("/cvmfs/neurodesk.ardc.edu.au/containers/demo_1.0_20260101"),
                "demo",
                bind_option,
            )
            for bind_option in (
                "",
                "--bind $TMP:/tmp",
                "--bind $TMPDIR:/tmp",
                "--bind $TEMP:/tmp",
                "--bind $TEMPDIR:/tmp",
            )
        ]
        variants.append(variants[0].replace("exec  ", "exec ", 1))

        for legacy in variants:
            with self.subTest(invocation=legacy.splitlines()[-1]):
                with tempfile.TemporaryDirectory() as directory:
                    repo_root = Path(directory) / "repo"
                    container = make_container(repo_root, ["demo"])
                    live_path = Path(
                        "/cvmfs/neurodesk.ardc.edu.au/containers"
                    ) / container.name
                    content = legacy.replace(str(live_path), str(container))
                    wrapper = write_wrapper(container, "demo", content)

                    plan = reconcile.plan_wrapper_reconciliation(repo_root)
                    self.assertEqual(plan.diagnostics, ())
                    self.assertEqual(len(plan.rewrites), 1)
                    reconcile.apply_wrapper_plan(plan)

                    wrapper_text = wrapper.read_text()
                    self.assertIn(XAUTHORITY_BLOCK, wrapper_text)
                    self.assertIn(
                        'singularity --silent exec "${xauthority_opts[@]}" ',
                        wrapper_text,
                    )

    def test_supports_generated_duplicate_display_variant(self):
        container = make_container(self.repo_root, ["bidscoin"])
        content = legacy_wrapper(container, "bidscoin").replace(
            "DISPLAY=$DISPLAY  $neurodesk_singularity_opts",
            "DISPLAY=$DISPLAY --env DISPLAY=$DISPLAY $neurodesk_singularity_opts",
            1,
        )
        wrapper = write_wrapper(container, "bidscoin", content)

        plan = reconcile.plan_wrapper_reconciliation(self.repo_root)

        self.assertEqual(plan.diagnostics, ())
        self.assertEqual(len(plan.rewrites), 1)
        reconcile.apply_wrapper_plan(plan)
        self.assertIn(XAUTHORITY_BLOCK, wrapper.read_text())

    def test_skips_fixed_disabled_missing_and_non_executable_targets(self):
        commands = ["fixed", "disabled", "missing", "metadata"]
        container = make_container(self.repo_root, commands)
        write_wrapper(container, "fixed", fixed_wrapper(container, "fixed"))
        write_wrapper(container, "disabled", disabled_wrapper(container))
        write_wrapper(container, "metadata", "not a wrapper\n", mode=0o644)

        plan = reconcile.plan_wrapper_reconciliation(self.repo_root)

        self.assertTrue(plan.is_clean)
        self.assertEqual(plan.rewrites, ())
        self.assertEqual(plan.diagnostics, ())

    def test_unknown_executable_blocks_every_planned_write(self):
        container = make_container(self.repo_root, ["legacy", "custom"])
        legacy = write_wrapper(
            container, "legacy", legacy_wrapper(container, "legacy")
        )
        write_wrapper(container, "custom", "#!/usr/bin/env bash\necho custom\n")
        before = legacy.read_bytes()

        plan = reconcile.plan_wrapper_reconciliation(self.repo_root)

        self.assertEqual(len(plan.rewrites), 1)
        self.assertEqual(len(plan.diagnostics), 1)
        with self.assertRaises(ValueError):
            reconcile.apply_wrapper_plan(plan)
        self.assertEqual(legacy.read_bytes(), before)

    def test_invalid_inventory_paths_block_every_planned_write(self):
        container = make_container(
            self.repo_root,
            ["legacy", "../escape", "/absolute", "nested/name", "back\\slash", "bad name"],
        )
        legacy = write_wrapper(
            container, "legacy", legacy_wrapper(container, "legacy")
        )
        before = legacy.read_bytes()

        plan = reconcile.plan_wrapper_reconciliation(self.repo_root)

        self.assertEqual(len(plan.rewrites), 1)
        self.assertEqual(len(plan.diagnostics), 5)
        with self.assertRaises(ValueError):
            reconcile.apply_wrapper_plan(plan)
        self.assertEqual(legacy.read_bytes(), before)

    def test_symlink_target_is_never_followed(self):
        container = make_container(self.repo_root, ["linked"])
        outside = Path(self.temporary_directory.name) / "outside"
        outside.write_text(legacy_wrapper(container, "linked"))
        outside.chmod(0o755)
        (container / "linked").symlink_to(outside)

        plan = reconcile.plan_wrapper_reconciliation(self.repo_root)

        self.assertEqual(plan.rewrites, ())
        self.assertEqual(len(plan.diagnostics), 1)
        self.assertEqual(outside.read_text(), legacy_wrapper(container, "linked"))

    def test_changed_since_plan_is_not_overwritten(self):
        container = make_container(self.repo_root, ["demo"])
        wrapper = write_wrapper(container, "demo", legacy_wrapper(container, "demo"))
        plan = reconcile.plan_wrapper_reconciliation(self.repo_root)
        wrapper.write_text("concurrent edit\n")

        with self.assertRaises(RuntimeError):
            reconcile.apply_wrapper_plan(plan)

        self.assertEqual(wrapper.read_text(), "concurrent edit\n")

    def test_cli_exit_status_distinguishes_clean_drift_and_error(self):
        container = make_container(self.repo_root, ["demo"])
        wrapper = write_wrapper(container, "demo", legacy_wrapper(container, "demo"))

        self.assertEqual(
            reconcile.main(["--repo-root", str(self.repo_root), "--check"]), 1
        )
        self.assertEqual(reconcile.main(["--repo-root", str(self.repo_root)]), 0)
        self.assertEqual(
            reconcile.main(["--repo-root", str(self.repo_root), "--check"]), 0
        )

        wrapper.write_text("#!/usr/bin/env bash\necho unknown\n")
        wrapper.chmod(0o755)
        self.assertEqual(
            reconcile.main(["--repo-root", str(self.repo_root), "--check"]), 2
        )

    def test_sync_runs_reconciler_after_stale_disabling_with_transaction_guard(self):
        sync = (ROOT / "cvmfs" / "sync_containers_to_cvmfs.sh").read_text()
        stale_publish = 'publish_cvmfs_transaction neurodesk.ardc.edu.au "disabled stale containers'
        reconciler = "reconcile_wrapper_xauthority.py"

        self.assertIn(reconciler, sync)
        self.assertLess(sync.index(stale_publish), sync.index(reconciler))
        self.assertIn("--check", sync[sync.index(reconciler) :])
        self.assertIn("open_cvmfs_transaction neurodesk.ardc.edu.au", sync[sync.index(reconciler) :])
        self.assertIn("abort_cvmfs_transaction neurodesk.ardc.edu.au", sync[sync.index(reconciler) :])
        self.assertIn("publish_cvmfs_transaction neurodesk.ardc.edu.au", sync[sync.index(reconciler) :])


if __name__ == "__main__":
    unittest.main()
