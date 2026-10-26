import builtins
import os
import sys

# Windowed builds have no standard streams; some solver libraries still use them.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# If PyInstaller is running, ensure environment variables or paths align
if getattr(sys, "frozen", False):
    os.environ["PYOMO_STRICT_PLUGINS"] = "0"


# PyInstaller doesn't run `site`, so these built-ins are missing in frozen apps.
for _name in ("help", "exit", "quit", "copyright", "credits", "license"):
    if not hasattr(builtins, _name):
        setattr(builtins, _name, lambda *a, **k: None)
