"""The dashboard reads __version__ via /api/version; a malformed version (empty,
non-string, wrong shape) would surface as a broken version label after deploy."""
import re

import parovanie


def test_version_is_semver_string():
    v = parovanie.__version__
    assert isinstance(v, str) and v
    # vMAJOR.MINOR.PATCH with an optional -dev.N suffix (matches version-on-dashboard.md)
    assert re.fullmatch(r"\d+\.\d+\.\d+(-dev\.\d+)?", v), v
