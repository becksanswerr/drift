"""Two real nodes on loopback, doing the whole round trip.

Discovery -> bid request -> bid -> execute over HTTP -> result callback.
If this file passes, the protocol works end to end.
"""

import time

import pytest

from drift import Node

PORT_A, PORT_B = 47401, 47402
HTTP_A, HTTP_B = 8811, 8812


def wait_for(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def hive():
    """`asker` has no capabilities; `worker` can shout."""
    asker = Node(name="asker", discovery="static", seeds=[f"127.0.0.1:{PORT_B}"],
                 discovery_port=PORT_A, http_port=HTTP_A,
                 advertise_ip="127.0.0.1", bind_host="127.0.0.1")
    worker = Node(name="worker", discovery="static", seeds=[f"127.0.0.1:{PORT_A}"],
                  discovery_port=PORT_B, http_port=HTTP_B,
                  advertise_ip="127.0.0.1", bind_host="127.0.0.1")

    @worker.capability("shout", cost=1)
    def shout(payload):
        return {"text": payload["text"].upper(), "by": "worker"}

    @worker.capability("boom")
    def boom(payload):
        raise ValueError("handler exploded")

    asker.start()
    worker.start()
    try:
        assert wait_for(lambda: asker.mesh.providers_of("shout")), "discovery failed"
        yield asker, worker
    finally:
        asker.stop()
        worker.stop()


def test_local_capability_runs_without_an_auction():
    node = Node(name="solo", discovery="static", discovery_port=47409,
                http_port=8819, advertise_ip="127.0.0.1", bind_host="127.0.0.1")
    node.register("double", lambda p: {"n": p["n"] * 2})
    with node:
        assert node.call("double", {"n": 21}) == {"n": 42}


def test_task_travels_to_the_only_node_that_can_run_it(hive):
    asker, _ = hive
    result = asker.call("shout", {"text": "hello hive"}, timeout=20)
    assert result == {"text": "HELLO HIVE", "by": "worker"}


def test_missing_capability_is_reported_not_hung(hive):
    asker, _ = hive
    result = asker.call("translate", {}, timeout=5, window=0.3)
    assert "no node provides" in result["error"]


def test_remote_handler_errors_come_back_as_errors(hive):
    asker, _ = hive
    result = asker.call("boom", {}, timeout=20)
    assert "handler exploded" in result["error"]


def test_prefer_remote_opens_an_auction_the_local_node_can_still_win(hive):
    """`prefer_remote` skips the shortcut, not the local node's right to bid."""
    asker, _ = hive
    asker.register("shout", lambda p: {"text": p["text"].upper(), "by": "asker"})
    local = asker.call("shout", {"text": "x"}, timeout=20)
    assert local["by"] == "asker"  # shortcut path

    routed = asker.call("shout", {"text": "x"}, timeout=20, prefer_remote=True, window=1.0)
    assert routed["by"] in {"asker", "worker"}  # whoever bid higher, but it ran


def test_active_task_count_is_visible_while_running(hive):
    asker, worker = hive
    started, release = [], []

    @worker.capability("slow")
    def slow(payload):
        started.append(True)
        while not release:
            time.sleep(0.02)
        return {"done": True}

    # A capability registered after startup only becomes routable once the next
    # beacon carries it — the peer table is the only thing the auction reads.
    assert wait_for(lambda: asker.mesh.providers_of("slow"))

    import threading

    thread = threading.Thread(target=lambda: asker.call("slow", {}, timeout=20), daemon=True)
    thread.start()
    assert wait_for(lambda: started)
    assert wait_for(lambda: worker.state()["active_tasks"] == 1)
    release.append(True)
    thread.join(timeout=10)
    assert wait_for(lambda: worker.state()["active_tasks"] == 0)


def test_state_endpoint_describes_the_node(hive):
    _, worker = hive
    state = worker.state()
    assert state["info"]["name"] == "worker"
    assert "shout" in state["info"]["capabilities"]
    assert state["info"]["http_port"] == HTTP_B


def test_second_node_on_a_taken_port_fails_clearly():
    first = Node(name="first", discovery="static", discovery_port=47421,
                 http_port=8821, bind_host="127.0.0.1")
    first.start()
    second = Node(name="second", discovery="static", discovery_port=47422,
                  http_port=8821, bind_host="127.0.0.1")
    try:
        with pytest.raises(RuntimeError, match="already in use"):
            second.start()
    finally:
        first.stop()
        second.stop()
