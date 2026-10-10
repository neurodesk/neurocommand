from pathlib import Path
import subprocess

from wrapper_legacy import WrapperState, _classify_wrapper

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "cvmfs" / "write_disabled_wrapper.sh"

REF = "freesurfer_8.2.0:20260818"
OLD_WRAPPER = (
    "#!/usr/bin/env bash\n"
    'echo "This container was disabled due to a known bug or vulnerability. To keep using the '
    "software please use a different version. If you absolutely need this container for "
    f'reproducibility you can pull it from docker hub via the command apptainer pull docker://vnmd/{REF}"\n'
)


def check(wrapper):
    return subprocess.run([SCRIPT, "--check", wrapper]).returncode


def write(wrapper):
    subprocess.run([SCRIPT, wrapper, REF], check=True)


def test_disabled_wrapper_fails_and_explains_on_stderr(tmp_path):
    wrapper = tmp_path / "recon-all"
    write(wrapper)
    result = subprocess.run([wrapper, "-all"], capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "This container was disabled" in result.stderr
    assert f"apptainer pull docker://vnmd/{REF}" in result.stderr


def test_check_flags_wrappers_that_still_exit_zero(tmp_path):
    wrapper = tmp_path / "recon-all"
    wrapper.write_text(OLD_WRAPPER)
    wrapper.chmod(0o755)
    assert subprocess.run([wrapper]).returncode == 0
    assert check(wrapper) == 1
    write(wrapper)
    assert check(wrapper) == 0


def test_check_rejects_active_wrappers(tmp_path):
    wrapper = tmp_path / "recon-all"
    wrapper.write_text('#!/usr/bin/env bash\nexec apptainer run image.simg recon-all "$@"\n')
    assert check(wrapper) == 1


def test_rewrite_is_idempotent(tmp_path):
    wrapper = tmp_path / "recon-all"
    write(wrapper)
    first = wrapper.read_bytes()
    write(wrapper)
    assert wrapper.read_bytes() == first


def test_wrapper_reconciliation_still_treats_new_form_as_disabled(tmp_path):
    container = tmp_path / "freesurfer_8.2.0_20260818"
    container.mkdir()
    wrapper = container / "recon-all"
    write(wrapper)
    state, _ = _classify_wrapper(container, "recon-all", wrapper.read_bytes())
    assert state is WrapperState.DISABLED
