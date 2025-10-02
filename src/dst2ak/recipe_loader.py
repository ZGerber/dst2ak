#!/usr/bin/env python3
"""
dst2ak.recipe_loader

Load TOML bank recipes into Python dicts.
"""

from pathlib import Path
import tomllib

# Ensure libclang is initialized if needed (comes from __init__.py)
from . import _auto_set_libclang  # noqa: F401


def load_recipe(path: Path) -> list[dict]:
    """Load a TOML recipe from file into a list of ops."""
    with open(path, "rb") as f:
        d = tomllib.load(f)
    return d["ops"]
