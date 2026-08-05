import pytest

from drift.security import NetworkSecurity

crypto = pytest.importorskip("cryptography")


def test_roundtrip():
    sec = NetworkSecurity("hunter2")
    assert sec.decrypt(sec.encrypt('{"a": 1}')) == '{"a": 1}'


def test_wrong_secret_is_dropped_not_raised():
    """A foreign packet must be dropped quietly — the LAN is full of them."""
    mine, theirs = NetworkSecurity("hunter2"), NetworkSecurity("something-else")
    assert mine.decrypt(theirs.encrypt("hello")) is None


def test_garbage_is_dropped():
    assert NetworkSecurity("hunter2").decrypt(b"\x00\x01not-a-token") is None


def test_no_secret_is_plaintext():
    sec = NetworkSecurity(None)
    assert sec.enabled is False
    assert sec.decrypt(sec.encrypt("hello")) == "hello"
    assert sec.check_token(None) is True  # open Hive accepts anyone


def test_http_token_is_distinct_from_datagram_key():
    sec = NetworkSecurity("hunter2")
    assert sec.token and sec.token not in sec.encrypt("x").decode("utf-8", "ignore")
    assert sec.check_token(sec.token) is True
    assert sec.check_token("wrong") is False
    assert sec.check_token(None) is False


def test_same_secret_yields_same_token_across_processes():
    assert NetworkSecurity("hunter2").token == NetworkSecurity("hunter2").token
