import pytest

from agent.probe import is_private_host


@pytest.mark.parametrize(
    "host, expected",
    [
        ("ppitest.internal.tpml.in", True),
        ("app.internal.local", True),
        ("localhost", True),
        ("db.internal.company.com", True),
        ("example.com", False),
        ("api.github.com", False),
    ],
)
def test_is_private_host(host, expected):
    assert is_private_host(host) is expected
