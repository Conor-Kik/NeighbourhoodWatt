import importlib
import pkgutil
from pathlib import Path

from frontend_v2.bindings.needs_decorator import SELECTOR_FUNCTIONS

pkg_dir = Path(__file__).parent
for module_info in pkgutil.iter_modules([str(pkg_dir)]):
    importlib.import_module(f".{module_info.name}", package=__name__)

# Global namespace
globals().update(SELECTOR_FUNCTIONS)

# Expose
__all__ = list(SELECTOR_FUNCTIONS.keys())
