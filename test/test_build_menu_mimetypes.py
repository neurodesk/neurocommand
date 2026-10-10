from pathlib import Path
import configparser
import shlex

from neurodesk.build_menu import EXEC_MIMETYPES, NeurodeskApp, write_app_menu, write_app_sh

def make_app(tmp_path, name, exec):
    (tmp_path / "icons").mkdir(exist_ok=True)
    app = NeurodeskApp(
        deskenv="lxde",
        installdir=tmp_path,
        name=name,
        category="libreoffice",
        command=exec,
    )
    write_app_sh(app)
    write_app_menu(app)
    return app


def read_desktop_entry(app):
    entry = configparser.ConfigParser(interpolation=None)
    entry.optionxform = str
    entry.read(app.installdir / "applications" / f"{app.basename}.desktop")
    return entry["Desktop Entry"]


def test_document_app_declares_mimetypes_and_field_code(tmp_path):
    app = make_app(tmp_path, "libreofficeWriterGUI-libreoffice 26.2.4", "lowriter")
    desktop = read_desktop_entry(app)
    assert desktop["Exec"].endswith(" %F")
    mimetypes = desktop["MimeType"]
    assert mimetypes.endswith(";")
    assert "application/vnd.oasis.opendocument.text;" in mimetypes
    assert (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document;"
        in mimetypes
    )
    # csv stays with text editors
    assert "text/csv" not in "".join(EXEC_MIMETYPES.get("localc", []))


def test_non_document_app_has_no_mimetypes(tmp_path):
    app = make_app(tmp_path, "fsleyesGUI-fsl 6.0.7.16", "fsleyes")
    desktop = read_desktop_entry(app)
    assert "MimeType" not in desktop
    assert "%F" not in desktop["Exec"]


def test_wrapper_script_quotes_forwarded_args(tmp_path):
    app = make_app(tmp_path, "libreofficeWriterGUI-libreoffice 26.2.4", "lowriter")
    sh_content = Path(app.sh_path).read_text()
    assert '"$@"' in sh_content


def test_wrapper_script_preserves_multiword_exec_arguments(tmp_path):
    app = make_app(
        tmp_path,
        "cat12GUI-cat12 12.9",
        "bash run_spm12.sh /opt/mcr/v93/",
    )
    command = Path(app.sh_path).read_text().splitlines()[1]

    assert shlex.split(command)[-6:] == [
        "cat12",
        "12.9",
        "bash",
        "run_spm12.sh",
        "/opt/mcr/v93/",
        "$@",
    ]


def test_wrapper_script_preserves_named_variant_container(tmp_path):
    app = make_app(tmp_path, "viewerGUI-tool_arm64 1.2.3", "viewer")
    sh_content = Path(app.sh_path).read_text()
    assert " tool_arm64 1.2.3 viewer" in sh_content


def test_wrapper_script_omits_empty_exec_argument(tmp_path):
    app = make_app(tmp_path, "tool_arm64 1.2.3", "")
    command = Path(app.sh_path).read_text().splitlines()[1]
    assert command.endswith('fetch_and_run.sh tool_arm64 1.2.3 "$@"')
    assert "''" not in command


def test_desktop_write_does_not_require_launcher_write(tmp_path):
    from dataclasses import FrozenInstanceError
    import pytest

    app = NeurodeskApp("lxde", tmp_path, "tool 1.2", category="tools")
    write_app_menu(app)
    assert read_desktop_entry(app)["Name"] == "tool 1.2"
    assert not app.sh_path.exists()
    write_app_sh(app)
    assert app.sh_path.stat().st_mode & 0o777 == 0o755
    assert app.desktop_path.stat().st_mode & 0o777 == 0o644
    app.sh_path.chmod(0o700)
    app.desktop_path.chmod(0o600)
    write_app_menu(app)
    write_app_sh(app)
    assert app.sh_path.stat().st_mode & 0o777 == 0o700
    assert app.desktop_path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FrozenInstanceError):
        app.name = "other"


def test_generated_launcher_runs_with_quoted_path_and_arguments(tmp_path):
    import json
    import subprocess

    install = tmp_path / "install with 'quotes' $literal"
    install.mkdir()
    fetch = install / "fetch_and_run.sh"
    fetch.write_text('#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n')
    fetch.chmod(0o755)
    app = NeurodeskApp(
        "lxde", install, "viewerGUI-tool_arm64 1.2", command="viewer --label 'space value'"
    )
    write_app_sh(app)
    result = subprocess.run([str(app.sh_path), "file with spaces", "$literal"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "tool_arm64", "1.2", "viewer", "--label", "space value", "file with spaces", "$literal"
    ]


def test_spec_copies_apptainer_arguments(tmp_path):
    arguments = ["--cleanenv"]
    app = NeurodeskApp("lxde", tmp_path, "tool 1.2", apptainer_args=arguments)
    arguments.append("--nv")
    assert app.apptainer_args == ("--cleanenv",)


def desktop_argv(app):
    from neurodesk.build_menu import render_app_menu

    entry = configparser.ConfigParser(interpolation=None)
    entry.read_string(render_app_menu(app))
    # Desktop Entry string escaping precedes Exec argument parsing.
    command = entry["Desktop Entry"]["Exec"].replace("\\\\", "\\")
    return [argument.replace("\\$", "$").replace("\\`", "`") for argument in shlex.split(command)]


def test_desktop_exec_quotes_install_paths_for_both_environments(tmp_path):
    install = tmp_path / "install with 'quotes' $literal"
    for environment in ("lxde", "mate"):
        app = NeurodeskApp(environment, install, "tool 1.2", terminal=False)
        arguments = desktop_argv(app)
        if environment == "mate":
            assert arguments[:4] == ["mate-terminal", "--window", "--title", "tool 1.2"]
            assert arguments[4] == "-e"
            assert shlex.split(arguments[5]) == ["/bin/bash", str(app.sh_path)]
        else:
            assert arguments == ["/bin/bash", str(app.sh_path)]


def test_custom_launcher_preserves_prefix_and_command(tmp_path):
    from neurodesk.build_menu import render_app_sh

    app = NeurodeskApp(
        "lxde", tmp_path, "Help", sh_prefix="env DEMO=1", launcher_command="printf 'help\\n'"
    )
    assert render_app_sh(app) == "#!/usr/bin/env bash\nenv DEMO=1 printf 'help\\n'\n"
    assert not (tmp_path / "bin").exists()
