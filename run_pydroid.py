"""Pydroid 3 entry point; shares port validation and startup checks with run.py.

Install requirements using Pydroid's Pip UI (python-docx may need its repository
plugin). Keep Pydroid running; opening a browser does not bypass Android's
background process/battery restrictions or automatically enable fullscreen.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from run import main

if __name__ == "__main__":
    raise SystemExit(main())
