from cvmfs import reconcile_module_files
from test.support.paths import ROOT, MODULE_RECONCILIATION_SCRIPT as SCRIPT


def make_container(repo_root, container_name, commands="datalad\n"):
    container = repo_root / "containers" / container_name
    container.mkdir(parents=True)
    (container / "commands.txt").write_text(commands)
