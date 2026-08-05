"""The HTTP surface: routes, auth, and the CLI's borrow-a-node path."""

import json
import urllib.error

import pytest

from drift import Node
from drift.dataplane import get_json, post_json

pytest.importorskip("cryptography")

PORT, HTTP = 47431, 8831
SECRET = "hunter2"


@pytest.fixture
def node():
    node = Node(name="host", discovery="static", discovery_port=PORT, http_port=HTTP,
                secret=SECRET, advertise_ip="127.0.0.1", bind_host="127.0.0.1")
    node.register("upper", lambda p: {"text": p["text"].upper()})
    with node:
        yield node


def url(path):
    return f"http://127.0.0.1:{HTTP}{path}"


def auth(node):
    return node.mesh.security.auth_header()


def test_health_and_info(node):
    assert get_json(url("/drift/health"), headers=auth(node))["ok"] is True
    info = get_json(url("/drift/info"), headers=auth(node))
    assert info["name"] == "host" and info["secured"] is True


def test_requests_without_the_token_are_rejected(node):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get_json(url("/drift/state"))
    assert exc.value.code == 401


def test_wrong_token_is_rejected(node):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get_json(url("/drift/state"), headers={"X-Drift-Auth": "nope"})
    assert exc.value.code == 401


def test_call_endpoint_runs_a_capability(node):
    response = post_json(url("/drift/call"),
                         {"cap": "upper", "payload": {"text": "hi"}},
                         headers=auth(node))
    assert response["result"] == {"text": "HI"}


def test_call_without_cap_is_a_bad_request(node):
    with pytest.raises(urllib.error.HTTPError) as exc:
        post_json(url("/drift/call"), {"payload": {}}, headers=auth(node))
    assert exc.value.code == 400


def test_execute_requires_a_task_id(node):
    with pytest.raises(urllib.error.HTTPError) as exc:
        post_json(url("/drift/execute"), {"cap": "upper"}, headers=auth(node))
    assert exc.value.code == 400


def test_unknown_route_is_404(node):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get_json(url("/nope"), headers=auth(node))
    assert exc.value.code == 404


def test_malformed_body_is_rejected(node):
    import urllib.request

    request = urllib.request.Request(url("/drift/call"), data=b"{not json", method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in auth(node).items():
        request.add_header(key, value)
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=5)
    assert exc.value.code == 400


def test_state_is_json_serialisable(node):
    json.dumps(node.state())
