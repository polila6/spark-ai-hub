"""Shared, session-scoped logins. The hub rate-limits logins (20 per IP per 5 min), so tests reuse sessions."""
import pytest

from client import Hub, admin


@pytest.fixture(scope="session")
def adm() -> Hub:
    return admin()


@pytest.fixture(scope="session")
def alice(adm) -> Hub:
    from test_acceptance import _make_researcher

    return _make_researcher(adm, "alice-test")


@pytest.fixture(scope="session")
def bob(adm) -> Hub:
    from test_acceptance import _make_researcher

    return _make_researcher(adm, "bob-test")
