"""Browser tests of the collections editor, in their own folder: Playwright's sync API keeps an event loop running
for the whole session, which the add-on tests in tests/ cannot share. Reuses the add-on fixtures from tests/."""

import importlib.util
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent / "tests"
sys.path.insert(0, str(TESTS))

_spec = importlib.util.spec_from_file_location("addon_conftest", TESTS / "conftest.py")
_addon_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_addon_conftest)

theframe = _addon_conftest.theframe
addon = _addon_conftest.addon
