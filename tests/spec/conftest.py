"""Spec tests are written BEFORE each milestone and stay red until it's built.

Markers come from the file name (test_m4_*.py -> m4), so you can check one milestone:
    .venv/bin/pytest tests/spec -m m4
Rule: never weaken or delete a spec assertion without the owner's explicit approval
(audit with `git log -p tests/spec`). Adding assertions is fine.
"""

import re

import pytest


def pytest_configure(config):
    for m in ("m4", "m5", "m6"):
        config.addinivalue_line("markers", f"{m}: spec for milestone {m.upper()}")


def pytest_collection_modifyitems(items):
    for item in items:
        m = re.search(r"test_(m\d)_", item.fspath.basename)
        if m:
            item.add_marker(getattr(pytest.mark, m.group(1)))
