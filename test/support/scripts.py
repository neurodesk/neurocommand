import importlib.util
from pathlib import Path
import sys
from types import ModuleType


def load_script(name: str, path: Path) -> ModuleType:
    """Load a fresh script module, retaining its name for dataclass resolution."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
