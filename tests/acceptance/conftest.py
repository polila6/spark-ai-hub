"""Shared, session-scoped logins. The hub rate-limits logins (20 per IP per 5 min), so tests reuse sessions."""
import pytest

from client import Hub, admin


@pytest.fixture(scope="session")
def adm() -> Hub:
    return admin()


@pytest.fixture(scope="session")
def alice(request) -> Hub:
    """SAH_TEST_USER (+ SAH_TEST_USER_PASSWORD) runs the researcher tests with an existing account, no admin needed."""
    import os

    if os.environ.get("SAH_TEST_USER"):
        return Hub(os.environ["SAH_TEST_USER"], os.environ["SAH_TEST_USER_PASSWORD"])
    from test_acceptance import _make_researcher

    return _make_researcher(request.getfixturevalue("adm"), "alice-test")


@pytest.fixture(scope="session")
def bob(adm) -> Hub:
    from test_acceptance import _make_researcher

    return _make_researcher(adm, "bob-test")
