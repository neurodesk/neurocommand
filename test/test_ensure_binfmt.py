import os
import shlex
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "cvmfs" / "ensure_binfmt.sh"


def run_helper(script, env=None):
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
    )


def test_native_container_does_not_install_binfmt(tmp_path):
    install_log = tmp_path / "install.log"
    result = run_helper(
        f"""
        source {shlex.quote(str(SCRIPT))}
        HOST_ARCH_OVERRIDE=x86_64
        install_foreign_arch_support() {{ echo called > {shlex.quote(str(install_log))}; }}
        ensure_container_architecture_support demo_1.0_20260813
        """
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert not install_log.exists()


def test_legacy_arm64_image_name_is_detected():
    result = run_helper(
        f"""
        source {shlex.quote(str(SCRIPT))}
        HOST_ARCH_OVERRIDE=x86_64
        container_target_architecture amico_2.1.0_arm64_20260512
        """
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.strip() == "aarch64"


def test_ready_arm64_handler_skips_install(tmp_path):
    binfmt_dir = tmp_path / "binfmt_misc"
    binfmt_dir.mkdir()
    interpreter = tmp_path / "qemu-aarch64-static"
    interpreter.write_text("#!/usr/bin/env bash\nexit 0\n")
    interpreter.chmod(0o755)
    (binfmt_dir / "qemu-aarch64").write_text(
        f"enabled\ninterpreter {interpreter}\nflags: POCF\n"
    )
    install_log = tmp_path / "install.log"

    result = run_helper(
        f"""
        source {shlex.quote(str(SCRIPT))}
        HOST_ARCH_OVERRIDE=x86_64
        BINFMT_MISC_DIR={shlex.quote(str(binfmt_dir))}
        install_foreign_arch_support() {{ echo called > {shlex.quote(str(install_log))}; }}
        ensure_container_architecture_support neurodesktop-lite_arm64_20260428_20260813
        """
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "qemu-aarch64" in result.stdout
    assert not install_log.exists()


def test_missing_arm64_handler_is_installed_and_rechecked(tmp_path):
    binfmt_dir = tmp_path / "binfmt_misc"
    binfmt_dir.mkdir()
    interpreter = tmp_path / "qemu-aarch64-static"
    install_log = tmp_path / "install.log"

    result = run_helper(
        f"""
        source {shlex.quote(str(SCRIPT))}
        HOST_ARCH_OVERRIDE=x86_64
        BINFMT_MISC_DIR={shlex.quote(str(binfmt_dir))}
        install_foreign_arch_support() {{
            echo "$1" > {shlex.quote(str(install_log))}
            printf '#!/usr/bin/env bash\nexit 0\n' > {shlex.quote(str(interpreter))}
            chmod +x {shlex.quote(str(interpreter))}
            printf 'enabled\ninterpreter %s\nflags: POCF\n' \
                {shlex.quote(str(interpreter))} > "$BINFMT_MISC_DIR/qemu-aarch64"
        }}
        ensure_container_architecture_support neurodesktop-lite_arm64_20260428_20260813
        """
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert install_log.read_text().strip() == "aarch64"
    assert "support installed" in result.stdout


def test_failed_system_install_uses_container_fallback(tmp_path):
    binfmt_dir = tmp_path / "binfmt_misc"
    binfmt_dir.mkdir()
    interpreter = tmp_path / "container-qemu"
    fallback_log = tmp_path / "fallback.log"

    result = run_helper(
        f"""
        source {shlex.quote(str(SCRIPT))}
        HOST_ARCH_OVERRIDE=x86_64
        BINFMT_MISC_DIR={shlex.quote(str(binfmt_dir))}
        install_foreign_arch_support() {{ return 1; }}
        install_binfmt_with_container_runtime() {{
            echo "$1" > {shlex.quote(str(fallback_log))}
            printf 'enabled\ninterpreter %s\nflags: F\n' \
                {shlex.quote(str(interpreter))} > "$BINFMT_MISC_DIR/qemu-aarch64"
        }}
        ensure_container_architecture_support neurodesktop-lite_arm64_20260428_20260813
        """
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert fallback_log.read_text().strip() == "aarch64"


def test_arm64_handler_without_fix_binary_flag_is_rejected(tmp_path):
    binfmt_dir = tmp_path / "binfmt_misc"
    binfmt_dir.mkdir()
    interpreter = tmp_path / "qemu-aarch64-static"
    interpreter.write_text("#!/usr/bin/env bash\nexit 0\n")
    interpreter.chmod(0o755)
    (binfmt_dir / "qemu-aarch64").write_text(
        f"enabled\ninterpreter {interpreter}\nflags: POC\n"
    )

    env = os.environ.copy()
    env["BINFMT_AUTO_INSTALL"] = "0"
    result = run_helper(
        f"""
        source {shlex.quote(str(SCRIPT))}
        HOST_ARCH_OVERRIDE=x86_64
        BINFMT_MISC_DIR={shlex.quote(str(binfmt_dir))}
        ensure_container_architecture_support neurodesktop-lite_arm64_20260428_20260813
        """,
        env=env,
    )

    assert result.returncode != 0
    assert "F flag" in result.stderr


def test_container_installer_uses_pinned_image_and_arm64_name(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    podman = bin_dir / "podman"
    podman.write_text(f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" > {calls}\n")
    podman.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    result = run_helper(
        f"""
        source {shlex.quote(str(SCRIPT))}
        run_privileged() {{ "$@"; }}
        install_binfmt_with_container_runtime aarch64
        """,
        env=env,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    call = calls.read_text().strip()
    assert call.startswith("run --privileged --rm docker.io/tonistiigi/binfmt:")
    assert "@sha256:" in call
    assert call.endswith("--install arm64")
