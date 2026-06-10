import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "neurodesk" / "transparent-singularity" / "ts_sanitize_lua_help.sh"


def sanitize(text):
    return subprocess.run(
        ["bash", str(SCRIPT)],
        input=text,
        capture_output=True,
        check=True,
        text=True,
    ).stdout


def test_sanitizes_lmod_cache_long_string_delimiter():
    output = sanitize(
        "usage: tool [--participant-label PARTICIPANT_LABEL [PARTICIPANT_LABEL ...]]\n"
        "       [-a ATLASES_DIR]\n"
    )

    assert "]]" not in output
    assert "[PARTICIPANT_LABEL ...] ]" in output
    assert "[-a ATLASES_DIR]" in output
