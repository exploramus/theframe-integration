"""Add-on and integration are released together under one version."""

import json
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def addon_version() -> str:
    return str(yaml.safe_load((ROOT / "theframe_controller" / "config.yaml").read_text())["version"])


def integration_version() -> str:
    return json.loads((ROOT / "custom_components" / "theframe" / "manifest.json").read_text())["version"]


def test_versions_match():
    assert addon_version() == integration_version()


def test_release_tag_matches_version():
    """On a release build, RELEASE_TAG (e.g. v1.1.0) must match both versions."""
    tag = os.environ.get("RELEASE_TAG")
    if not tag:
        return
    assert tag.removeprefix("v") == addon_version() == integration_version()


def test_changelog_has_version():
    changelog = (ROOT / "theframe_controller" / "CHANGELOG.md").read_text()
    assert any(line.startswith(addon_version() + " ") for line in changelog.splitlines())
