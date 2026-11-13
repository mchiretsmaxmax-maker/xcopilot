import importlib
import os
import pkgutil

import pytest

import xcopilot


def test_all_modules_import(capsys: pytest.CaptureFixture[str]) -> None:
    env_before = dict(os.environ)
    names = [m.name for m in pkgutil.walk_packages(xcopilot.__path__, "xcopilot.")]
    assert "xcopilot.config" in names
    for name in names:
        importlib.import_module(name)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    assert dict(os.environ) == env_before
