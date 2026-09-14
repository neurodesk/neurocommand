import os
import subprocess
from pathlib import Path

import pytest

from cvmfs import reconcile_module_files


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "neurodesk"
    / "transparent-singularity"
    / "ts_command_metadata.sh"
)


def render(commands_path):
    return subprocess.run(
        ["bash", str(SCRIPT), str(commands_path)],
        capture_output=True,
        text=True,
    )


def test_renders_sorted_unique_whatis_commands_and_skips_invalid_names(
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

    result = render(commands)

    assert result.returncode == 0
    assert result.stdout == (
        "-- neurodesk-exposed-commands\n"
        'whatis("Commands: bad,command, bet, flirt, quoted\\\"command")\n'
    )
    assert result.stderr.count("Skipping invalid command name") == 2


def test_empty_command_inventory_emits_no_metadata_block(tmp_path):
    commands = tmp_path / "commands.txt"
    commands.write_text("")

    result = render(commands)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize("renderer", ["install", "reconcile-lua", "reconcile-tcl"])
@pytest.mark.parametrize("include_commands", [False, True])
def test_discovery_omits_libraries_and_hidden_files(tmp_path, renderer, include_commands):
    excluded = [
        "model_beta.so", "plug_betafit.so", "libmwoauth_connector_betaBuiltins.so",
        "libbeta.so.1.2", "libbeta.dylib", "beta.DLL",
        ".fsl-bet2-post-link.sh", ".fsl-bet2-pre-unlink.sh",
    ]
    kept = ["bet", "bet.fsl", "obj2_bet.pl", "Bet_gui", "libtool", "beta.software"]
    commands = tmp_path / "commands.txt"
    inventory = "\n".join(excluded + (kept if include_commands else [])) + "\n"
    commands.write_text(inventory)

    if renderer == "install":
        result = render(commands)
        assert result.returncode == 0
        output = result.stdout
    else:
        output = reconcile_module_files.update_exposed_commands(
            "", commands, is_lua=renderer == "reconcile-lua"
        )
        assert reconcile_module_files.update_exposed_commands(
            output, commands, is_lua=renderer == "reconcile-lua"
        ) == output

    for name in excluded:
        assert name not in output
    if include_commands:
        for name in kept:
            assert name in output
    else:
        assert output == ""
    assert commands.read_text() == inventory


@pytest.mark.parametrize(
    "is_lua, old_block",
    [
        (True, 'extensions("old/1.0")\n'),
        (True, 'if type(extensions) == "function" then\n'
         '    extensions("old/1.0")\nend\n'),
        (False, 'extensions "old/1.0"\n'),
        (False, 'if {[llength [info commands extensions]] > 0} {\n'
         '    extensions "old/1.0"\n}\n'),
        (True, 'whatis("Commands: old")\n'),
        (False, 'module-whatis "Commands: old"\n'),
    ],
)
def test_migrates_managed_metadata_and_removes_it_when_empty(tmp_path, is_lua, old_block):
    commands = tmp_path / "commands.txt"
    commands.write_text("bet\nmodel_beta.so\n")
    prefix = "--" if is_lua else "#"
    other_metadata = (
        'whatis("Description: FSL")\nextensions("unrelated/1.0")\n'
        if is_lua else
        '#%Module\nmodule-whatis "Description: FSL"\nextensions "unrelated/1.0"\n'
    )
    content = other_metadata + f"{prefix} neurodesk-exposed-commands\n" + old_block
    updated = reconcile_module_files.update_exposed_commands(
        content, commands, is_lua=is_lua
    )
    assert "old" not in updated
    assert "model_beta" not in updated
    assert updated.count("Commands: bet") == 1
    assert updated.count("extensions") == 1  # Only the unrelated extension remains.
    assert reconcile_module_files.update_exposed_commands(
        updated, commands, is_lua=is_lua
    ) == updated

    commands.write_text("")
    assert reconcile_module_files.update_exposed_commands(
        updated, commands, is_lua=is_lua
    ) == other_metadata


def test_install_and_reconciliation_escape_the_same_command_names(tmp_path):
    commands = tmp_path / "commands.txt"
    commands.write_text('quoted"command\nback\\slash\n$variable\n[brackets]\n')
    assert render(commands).stdout == (
        reconcile_module_files.render_exposed_commands(commands) + "\n"
    )
    tcl = reconcile_module_files.render_exposed_commands(commands, is_lua=False)
    assert r'\$variable' in tcl
    assert r'\[brackets\]' in tcl
    assert r'back\\slash' in tcl
    assert r'quoted\"command' in tcl


@pytest.mark.skipif(not os.environ.get("LMOD_CMD"), reason="Lmod is not initialized")
@pytest.mark.parametrize("renderer", ["install", "reconcile-lua", "reconcile-tcl"])
def test_lmod_keyword_finds_provider_without_extensions(tmp_path, renderer):
    commands = tmp_path / "commands.txt"
    commands.write_text("bet\nflirt\nmodel_beta.so\n.fsl-bet2-post-link.sh\n")
    module_dir = tmp_path / "modules" / "fsl"
    module_dir.mkdir(parents=True)
    is_lua = renderer != "reconcile-tcl"
    description = (
        'whatis("Description: FSL tools")\n' if is_lua else
        '#%Module\nmodule-whatis "Description: FSL tools"\n'
    )
    if renderer == "install":
        content = description + render(commands).stdout
    else:
        old_block = (
            '-- neurodesk-exposed-commands\nextensions("bet/6.0.7.18")\n'
            if is_lua else
            '# neurodesk-exposed-commands\nextensions "bet/6.0.7.18"\n'
        )
        content = reconcile_module_files.update_exposed_commands(
            description + old_block, commands, is_lua=is_lua
        )
    (module_dir / ("6.0.7.18.lua" if is_lua else "6.0.7.18")).write_text(content)
    env = {
        **os.environ,
        "MODULEPATH": str(module_dir.parent),
        "LMOD_IGNORE_CACHE": "yes",
        "LMOD_COLORIZE": "no",
        "LMOD_PAGER": "none",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    for query, finds_provider in [
        (["keyword", "bet"], True),
        (["keyword", "model_beta.so"], False),
        (["avail", "bet"], False),
    ]:
        result = subprocess.run(
            [env["LMOD_CMD"], "bash", *query],
            env=env, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert ("fsl/6.0.7.18" in result.stderr) == finds_provider
        assert "(E)" not in result.stderr
