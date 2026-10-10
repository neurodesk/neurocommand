from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRANSPARENT_SINGULARITY = ROOT / "neurodesk/transparent-singularity"
MODULE_RECONCILIATION_SCRIPT = ROOT / "cvmfs/reconcile_module_files.py"
