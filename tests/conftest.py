"""Vendoring tests run against a fake commit id in a temporary demo; the git binding that would refuse that is switched off
here, per test, by the one environment variable the real tool honours (there is no command-line flag for it)."""

import pytest


@pytest.fixture(autouse=True)
def _allow_fake_commits(monkeypatch):
    monkeypatch.setenv("HADAVIN_VENDOR_ALLOW_UNMERGED", "1")


# --------------------------------------------------------------------------- the cut test: demo_only/ deleted
import pathlib

DEMO_DIR = pathlib.Path(__file__).resolve().parents[1] / ("demo" + "_only")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """With the sample-data folder deleted, a test that needs a sample file is SKIPPED with a clear reason (never silently
    passed, never an error); a missing file anywhere else still fails."""
    outcome = yield
    if DEMO_DIR.is_dir() or outcome.excinfo is None:
        return
    exc = outcome.excinfo[1]
    if isinstance(exc, OSError) and DEMO_DIR.name in str(exc):
        outcome.force_exception(pytest.skip.Exception("needs the sample-data folder (cut test)"))


def pytest_collection_modifyitems(config, items):
    if DEMO_DIR.is_dir():
        return
    skip = pytest.mark.skip(reason="needs the sample-data folder (cut test)")
    for item in items:
        if "needs_samples" in item.keywords:
            item.add_marker(skip)
