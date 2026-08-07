import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "neurodesk"
    / "transparent-singularity"
    / "ts_lmod_extensions.sh"
)


def render(commands_path, version):
    return subprocess.run(
        ["bash", str(SCRIPT), str(commands_path), version],
        capture_output=True,
        text=True,
    )


def test_renders_sorted_unique_lmod_extensions_and_skips_unrepresentable_names(
    tmp_path,
):
    commands = tmp_path / "commands.txt"
    commands.write_text(
        "flirt\n"
        "bet\n"
        "bet\n"
        "bad/name\n"
        "bad,command\n"
        "bad command\n"
        'quoted"command\n'
    )

    result = render(commands, "6.0.7.18")

    assert result.returncode == 0
    assert result.stdout == (
        "-- neurodesk-exposed-commands\n"
        'if type(extensions) == "function" then\n'
        '    extensions("bet/6.0.7.18, flirt/6.0.7.18, '
        'quoted\\\"command/6.0.7.18")\n'
        "end\n"
    )
    assert result.stderr.count("cannot be represented as an Lmod extension") == 3


def test_empty_command_inventory_emits_no_extension_block(tmp_path):
    commands = tmp_path / "commands.txt"
    commands.write_text("")

    result = render(commands, "1.0")

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_rejects_invalid_extension_version(tmp_path):
    commands = tmp_path / "commands.txt"
    commands.write_text("demo\n")

    result = render(commands, "bad/version")

    assert result.returncode == 2
    assert result.stdout == ""
    assert "Invalid Lmod extension version" in result.stderr
