from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import pytest

from test.test_cvmfs_reconcile_module_files import ROOT, SCRIPT, make_container
from artifact_renderer import legacy_module_content, read_container_inventory


@pytest.fixture
def maintained_freesurfer_modules(tmp_path):
    old = "freesurfer_8.2.0_20260818"
    current = "freesurfer_8.2.0_20261005"
    snippets = ROOT / "neurodesk/transparent-singularity/manual_module_files"
    lua = (snippets / "freesurfer").read_text().rstrip("\n")
    tcl = (snippets / "tcl/freesurfer").read_text().rstrip("\n")
    for name in (old, current):
        make_container(tmp_path, name, "recon-all\nfreeview\n")
        directory = tmp_path / "containers" / name
        (directory / "env.txt").write_text("DEPLOY_ENV_FREESURFER_HOME=BASEPATH/opt/freesurfer\n")
        (directory / "README.md").write_text(f"FreeSurfer build {name}\n")
        (directory / "manual_module_files").mkdir()
        (directory / "manual_module_files/freesurfer").write_text(
            'local additional_bind_paths = "/tmp:/scratch"\n'
        )
    old_spec = replace(
        read_container_inventory(tmp_path / "containers" / old),
        manual_lua=lua,
        manual_tcl=tcl,
    )
    modules = []
    for root in (
        "containers/modules",
        "neurodesk-modules/image_segmentation",
        "neurodesk-modules/structural_imaging",
    ):
        for format, suffix in (("lua", ".lua"), ("tcl", "")):
            path = tmp_path / root / "freesurfer" / ("8.2.0" + suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(legacy_module_content(
                old_spec, Path("/cvmfs/neurodesk.ardc.edu.au/containers") / old,
                format=format,
            ))
            modules.append(path)
    log = tmp_path / "log.txt"
    log.write_text(f"{current} categories:image segmentation,structural imaging,\n")
    return modules, log


def reconcile(repo_root, log, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(repo_root), "--log", str(log), *args],
        capture_output=True, text=True,
    )


def test_maintained_snippets_advance_all_freesurfer_modules(tmp_path, maintained_freesurfer_modules):
    modules, log = maintained_freesurfer_modules
    pending = reconcile(tmp_path, log, "--check")
    assert pending.returncode == 1, pending.stdout + pending.stderr
    applied = reconcile(tmp_path, log)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    for module in modules:
        content = module.read_text()
        assert "freesurfer_8.2.0_20261005" in content
        assert "freesurfer_8.2.0_20260818" not in content
        assert "/scratch" in content
        assert "neurodesk-artifact-sha256" in content
    assert reconcile(tmp_path, log, "--check").returncode == 0


@pytest.mark.parametrize("edit", ["snippet", "footer", "environment"])
def test_maintained_snippet_migration_preserves_site_edits(
    tmp_path, maintained_freesurfer_modules, edit,
):
    modules, log = maintained_freesurfer_modules
    before = {}
    for module in modules:
        content = module.read_text()
        if edit == "snippet":
            content = content.replace('"/tmp"', '"/site-temp"')
        elif edit == "environment":
            content = content.replace("/opt/freesurfer", "/site/freesurfer")
        else:
            content += ('setenv("SITE_LICENSE", "/site/license")\n'
                        if module.suffix == ".lua"
                        else 'setenv SITE_LICENSE /site/license\n')
        module.write_text(content)
        before[module] = module.read_bytes()
    applied = reconcile(tmp_path, log)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert {module: module.read_bytes() for module in modules} == before
