"""Frozen recognition of historical generated wrappers. Never edit templates."""
from pathlib import Path
from enum import Enum
import shlex

NVIDIA_BLOCK = (
    b'if [ -f /proc/driver/nvidia/version ] && [ -z "${APPTAINER_NV+set}" ] '
    b'&& [ -z "${SINGULARITY_NV+set}" ]; then\n'
    b"  export APPTAINER_NV=1\n"
    b"  export SINGULARITY_NV=1\n"
    b"fi\n"
)

XAUTHORITY_BLOCK = (
    b"xauthority_opts=()\n"
    b'if [[ -n "${XAUTHORITY:-}" && -f "$XAUTHORITY" ]]; then\n'
    b'  xauthority_opts=(--bind "$XAUTHORITY:$XAUTHORITY:ro" '
    b'--env "XAUTHORITY=$XAUTHORITY")\n'
    b"fi\n"
)

XAUTHORITY_ARGUMENT = b'"${xauthority_opts[@]}" '

WRAPPER_SETUP_MARKERS = (b"xauthority_opts", b"XAUTHORITY", b"APPTAINER_NV", b"SINGULARITY_NV")

DISABLED_NOTICE = b"This container was disabled due to a known bug or vulnerability."

DISABLED_PULL_HINT = b"apptainer pull docker://vnmd/"

GENERATED_BIND_OPTIONS = (
    "",
    "--bind $TMP:/tmp",
    "--bind $TMPDIR:/tmp",
    "--bind $TEMP:/tmp",
    "--bind $TEMPDIR:/tmp",
)

class WrapperState(Enum):
    LEGACY = "legacy"
    FIXED = "fixed"
    DISABLED = "disabled"
    UNKNOWN = "unknown"

def _legacy_wrapper(container_dir: Path, command: str, bind_option: str) -> bytes:
    container_name = container_dir.name
    text = (
        "#!/usr/bin/env bash\n"
        "export PWD=`pwd -P`\n"
        "singularity --silent exec --cleanenv --env DISPLAY=$DISPLAY "
        f"{bind_option} $neurodesk_singularity_opts --pwd \"$PWD\" "
        f"{container_dir}/{container_name}.simg {command} \"$@\"\n"
    )
    return text.encode("utf-8")

def _legacy_wrapper_without_display(
    container_dir: Path, command: str, bind_option: str
) -> bytes:
    container_name = container_dir.name
    text = (
        "#!/usr/bin/env bash\n"
        "export PWD=`pwd -P`\n"
        f"singularity --silent exec {bind_option} $neurodesk_singularity_opts "
        f"--pwd \"$PWD\" {container_dir}/{container_name}.simg {command} \"$@\"\n"
    )
    return text.encode("utf-8")

def _legacy_wrapper_with_duplicate_display(container_dir: Path, command: str) -> bytes:
    container_name = container_dir.name
    text = (
        "#!/usr/bin/env bash\n"
        "export PWD=`pwd -P`\n"
        "singularity --silent exec --cleanenv --env DISPLAY=$DISPLAY "
        "--env DISPLAY=$DISPLAY $neurodesk_singularity_opts --pwd \"$PWD\" "
        f"{container_dir}/{container_name}.simg {command} \"$@\"\n"
    )
    return text.encode("utf-8")

def _legacy_wrapper_with_trailing_bind_slot(container_dir: Path, command: str) -> bytes:
    container_name = container_dir.name
    text = (
        "#!/usr/bin/env bash\n"
        "export PWD=`pwd -P`\n"
        "singularity --silent exec --cleanenv --env DISPLAY=$DISPLAY "
        "$neurodesk_singularity_opts  --pwd \"$PWD\" "
        f"{container_dir}/{container_name}.simg {command} \"$@\"\n"
    )
    return text.encode("utf-8")

def _legacy_wrapper_candidates(container_dir: Path, command: str) -> tuple[bytes, ...]:
    candidates = [
        _legacy_wrapper(container_dir, command, bind_option)
        for bind_option in GENERATED_BIND_OPTIONS
    ]
    candidates.extend(
        _legacy_wrapper_without_display(container_dir, command, bind_option)
        for bind_option in GENERATED_BIND_OPTIONS
    )

    # Wrappers generated before temporary-directory binding was introduced do
    # not contain the empty bind-variable slot (and therefore have one space at
    # the insertion point instead of two).
    candidates.append(
        _legacy_wrapper(container_dir, command, "").replace(
            b"DISPLAY=$DISPLAY  ", b"DISPLAY=$DISPLAY ", 1
        )
    )
    candidates.append(
        _legacy_wrapper_without_display(container_dir, command, "").replace(
            b"singularity --silent exec  ", b"singularity --silent exec ", 1
        )
    )
    candidates.append(_legacy_wrapper_with_duplicate_display(container_dir, command))
    candidates.append(_legacy_wrapper_with_trailing_bind_slot(container_dir, command))
    return tuple(dict.fromkeys(candidates))

def _xauthority_wrapper(legacy: bytes) -> bytes:
    pwd_line = b"export PWD=`pwd -P`\n"
    display_argument = b"--env DISPLAY=$DISPLAY "
    replacement = legacy.replace(pwd_line, pwd_line + XAUTHORITY_BLOCK, 1)
    if display_argument in replacement:
        return replacement.replace(
            display_argument,
            display_argument + XAUTHORITY_ARGUMENT,
            1,
        )
    return replacement.replace(
        b"singularity --silent exec ",
        b"singularity --silent exec " + XAUTHORITY_ARGUMENT,
        1,
    )

def _fixed_wrapper(legacy: bytes) -> bytes:
    pwd_line = b"export PWD=`pwd -P`\n"
    return _xauthority_wrapper(legacy).replace(pwd_line, pwd_line + NVIDIA_BLOCK, 1)

def _inventory_wrapper(container_dir: Path, command: str, *, legacy: bool = False) -> bytes:
    def quote(value: str) -> str:
        return "'" + value.replace("'", "'\\'" + "'") + "'"

    setup = (
        b"#!/usr/bin/env bash\nexport PWD=`pwd -P`\n"
        + NVIDIA_BLOCK + XAUTHORITY_BLOCK
        + b'tmp_opts=()\n'
        + b'for customtmp in TMP TMPDIR TEMP TEMPDIR; do\n'
        + b'  if [[ -n "${!customtmp}" ]]; then\n'
        + b'    tmp_opts=(--bind "${!customtmp}:/tmp")\n'
        + b'  fi\ndone\n'
    )
    gui_options = "" if legacy else '--env DISPLAY="$DISPLAY" "${xauthority_opts[@]}" '
    invocation = (
        "singularity --silent exec --cleanenv " + gui_options
        + '"${tmp_opts[@]}" '
        + '$neurodesk_singularity_opts --pwd "$PWD" '
        + quote(str(container_dir / f"{container_dir.name}.simg"))
        + " " + quote(command) + ' "$@"\n'
    )
    return setup + invocation.encode("utf-8")

def _v2_wrapper(container_dir: Path, command: str, *, legacy: bool = False) -> bytes:
    prefix = 'NEURODESK_CONTAINER_LEGACY_ENV=1 ' if legacy else ''
    return ('''#!/usr/bin/env bash
# neurodesk-artifact-v2
_neurodesk_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
source "$_neurodesk_dir/container_runtime.sh" || exit 2
''' + prefix + 'neurodesk_container exec "$_neurodesk_dir/"' + shlex.quote(container_dir.name + '.simg') + ' ' + shlex.quote(command) + ' "$@"\n').encode()


def _classify_wrapper(
    container_dir: Path, command: str, content: bytes
) -> tuple[WrapperState, bytes | None]:
    if (
        content.startswith(b"#!/usr/bin/env bash\n")
        and DISABLED_NOTICE in content
        and DISABLED_PULL_HINT in content
    ):
        return WrapperState.DISABLED, None

    if content in (_v2_wrapper(container_dir, command), _v2_wrapper(container_dir, command, legacy=True)):
        return WrapperState.FIXED, None

    if content in (_inventory_wrapper(container_dir, command), _inventory_wrapper(container_dir, command, legacy=True)):
        return WrapperState.FIXED, None

    for legacy in _legacy_wrapper_candidates(container_dir, command):
        if content in (legacy, _xauthority_wrapper(legacy)):
            return WrapperState.LEGACY, _fixed_wrapper(legacy)
        if content == _fixed_wrapper(legacy):
            return WrapperState.FIXED, None

    return WrapperState.UNKNOWN, None


def classify_relocated_wrapper(container_dir: Path, command: str, content: bytes):
    """Recover only an exact frozen wrapper with the same image identity."""
    state, replacement = _classify_wrapper(container_dir, command, content)
    if state is not WrapperState.UNKNOWN:
        return state, replacement
    try:
        words = shlex.split(content.decode().splitlines()[-1])
        image = Path(words[-3])
    except (ValueError, UnicodeDecodeError, IndexError):
        return state, replacement
    if image.name == container_dir.name + '.simg' and image.parent.name == container_dir.name:
        return _classify_wrapper(image.parent, command, content)
    return state, replacement
