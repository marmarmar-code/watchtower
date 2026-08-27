#!/usr/bin/env python3
"""Validate that the public source catalog matches the built-in adapters."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watchtower.source_catalog import validate_catalog


if __name__ == "__main__":
    validate_catalog()
    print("source catalog: OK")
