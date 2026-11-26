"""The renamed modules keep working under their old names, with a deprecation warning."""

import importlib
import sys

import pytest


@pytest.mark.parametrize(
    ("old", "new", "name"),
    [
        ("clabe.git_manager", "clabe.repository", "GitRepository"),
        ("clabe.cache_manager", "clabe.cache", "CacheManager"),
        ("clabe.services", "clabe.settings", "ServiceSettings"),
    ],
)
def test_an_old_module_name_is_the_new_module(old, new, name):
    sys.modules.pop(old, None)
    with pytest.warns(DeprecationWarning, match=new):
        module = importlib.import_module(old)
    assert module is importlib.import_module(new)
    assert getattr(module, name) is getattr(importlib.import_module(new), name)
