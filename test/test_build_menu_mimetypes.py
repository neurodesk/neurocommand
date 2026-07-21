import configparser
from pathlib import Path

from neurodesk.build_menu import EXEC_MIMETYPES, NeurodeskApp


def make_app(tmp_path, name, exec):
    (tmp_path / "icons").mkdir(exist_ok=True)
    app = NeurodeskApp(
        deskenv="lxde",
        installdir=tmp_path,
        name=name,
        category="libreoffice",
        exec=exec,
    )
    app.app_names()
    app.add_app_sh()
    app.add_app_menu()
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


def test_wrapper_script_preserves_named_variant_container(tmp_path):
    app = make_app(tmp_path, "viewerGUI-tool_arm64 1.2.3", "viewer")
    sh_content = Path(app.sh_path).read_text()
    assert " tool_arm64 1.2.3 viewer" in sh_content
