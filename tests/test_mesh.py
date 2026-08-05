"""Discovery over loopback.

Static discovery is used throughout: a test suite must not spray broadcast
packets onto whatever network the developer happens to be on.
"""

import time

import pytest

from drift.mesh import MeshNode

PORT_A, PORT_B = 47301, 47302


@pytest.fixture
def pair():
    a = MeshNode(name="alpha", caps=["llm"], discovery="static",
                 seeds=[f"127.0.0.1:{PORT_B}"], discovery_port=PORT_A, http_port=8801)
    b = MeshNode(name="beta", caps=["tts", "vision"], discovery="static",
                 seeds=[f"127.0.0.1:{PORT_A}"], discovery_port=PORT_B, http_port=8802)
    a.start()
    b.start()
    try:
        yield a, b
    finally:
        a.stop()
        b.stop()


def wait_for(predicate, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_nodes_find_each_other(pair):
    a, b = pair
    assert wait_for(lambda: a.peers and b.peers), "peers never appeared"
    peer = next(iter(a.peers.values()))
    assert peer["name"] == "beta"
    assert peer["ip"] == "127.0.0.1"
    assert sorted(peer["caps"]) == ["tts", "vision"]
    assert peer["http_port"] == 8802


def test_providers_of_filters_by_capability(pair):
    a, b = pair
    assert wait_for(lambda: a.peers)
    assert [p["name"] for p in a.providers_of("tts")] == ["beta"]
    assert a.providers_of("llm") == []  # itself is never a "provider" peer


def test_control_messages_route_to_handlers(pair):
    a, b = pair
    assert wait_for(lambda: a.peers and b.peers)
    received = []
    b.register_control_handler("ping", lambda msg, addr: received.append(msg))
    a.send_control("127.0.0.1", PORT_B, {"type": "ping", "value": 42})
    assert wait_for(lambda: received)
    assert received[0]["value"] == 42
    assert received[0]["node_id"] == a.node_id


def test_a_node_ignores_its_own_beacons(pair):
    a, _ = pair
    assert wait_for(lambda: a.peers)
    assert a.node_id not in a.peers


def test_peers_expire_when_beacons_stop(pair):
    a, b = pair
    assert wait_for(lambda: a.peers)
    b.stop()
    for peer in a.peers.values():
        peer["last_seen"] = time.time() - 999  # don't wait out PEER_TIMEOUT
    assert wait_for(lambda: not a.peers, timeout=5)


def test_secret_mismatch_makes_nodes_invisible_to_each_other():
    pytest.importorskip("cryptography")
    a = MeshNode(name="a", discovery="static", seeds=["127.0.0.1:47312"],
                 discovery_port=47311, secret="one")
    b = MeshNode(name="b", discovery="static", seeds=["127.0.0.1:47311"],
                 discovery_port=47312, secret="two")
    a.start()
    b.start()
    try:
        time.sleep(3)
        assert not a.peers and not b.peers
    finally:
        a.stop()
        b.stop()
