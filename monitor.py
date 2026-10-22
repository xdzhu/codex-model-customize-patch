#!/usr/bin/env python3
"""Public command entry; implementation and internal checks live in src/."""
from pathlib import Path
import importlib.util
import runpy
import sys

source = Path(__file__).resolve().parent / "src" / "monitor.py"
sys.path.insert(0, str(source.parent))
if __name__ == "__main__":
    runpy.run_path(str(source), run_name="__main__")
else:
    spec = importlib.util.spec_from_file_location(__name__, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[__name__] = module
    spec.loader.exec_module(module)
    globals().update(module.__dict__)
