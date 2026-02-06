"""Intentional failing test to verify CI blocks merge."""


def test_this_should_fail():
    assert 1 == 2, "This test intentionally fails to verify CI blocks the merge"
