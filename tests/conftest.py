"""Test-wide isolation.

Several suites create throwaway git repositories and commit into them. Without
this, the machine's global git config follows them in — and on any machine with
commit signing enforced, eleven of them error before asserting anything, naming
gpg rather than the fixture. A contributor's first `pytest` then looks like
their git setup is broken.

Isolating here rather than in each fixture covers the tests that do not exist
yet, which is the point.
"""

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _git_without_the_machine():
    """Run every git call against empty global and system config."""
    previous = {
        name: os.environ.get(name)
        for name in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")
    }
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
