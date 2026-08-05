"""Auction logic against a fake mesh — no sockets, no sleeping for a window."""

from drift.auction import Auctioneer


class FakeMesh:
    """Records what would have gone on the wire and lets tests answer it."""

    def __init__(self, node_id="local", name="local", providers=()):
        self.node_id = node_id
        self.name = name
        self.http_port = 8700
        self.discovery_port = 47100
        self._providers = list(providers)
        self.sent = []
        self.handlers = {}
        self.autoresponders = {}  # node_id -> score

    def register_control_handler(self, msg_type, fn):
        self.handlers[msg_type] = fn

    def providers_of(self, cap):
        return [dict(p) for p in self._providers]

    def send_control(self, ip, port, message):
        self.sent.append((ip, port, message))
        if message.get("type") != "bid_request":
            return
        # Simulate peers answering immediately so elect() returns without
        # waiting out the window.
        for node_id, score in self.autoresponders.items():
            self.handlers["bid"](
                {"type": "bid", "task_id": message["task_id"], "node_id": node_id,
                 "score": score, "name": node_id, "http_port": 8700,
                 "why": {"model": "gemma3"}},
                (ip, port),
            )


def build(providers=(), local_score=0, can_serve=True):
    mesh = FakeMesh(providers=providers)
    auctioneer = Auctioneer(
        mesh,
        can_serve=lambda cap: can_serve,
        score_fn=lambda cap, hint=None: local_score,
        bid_detail=lambda cap, hint=None: {"local": True},
    )
    return mesh, auctioneer


def test_no_providers_and_no_local_capability_means_no_winner():
    _, auctioneer = build(can_serve=False)
    assert auctioneer.elect("llm", window=0.05) is None


def test_local_node_can_win_its_own_auction():
    _, auctioneer = build(local_score=90)
    winner = auctioneer.elect("llm", window=0.05)
    assert winner["node_id"] == "local"
    assert winner["score"] == 90
    assert winner["task_id"]


def test_highest_bidder_wins():
    mesh, auctioneer = build(providers=[{"ip": "10.0.0.2", "disc_port": 47100}], local_score=40)
    mesh.autoresponders = {"remote": 88}
    winner = auctioneer.elect("llm", window=0.5)
    assert winner["node_id"] == "remote"
    assert winner["score"] == 88


def test_local_node_wins_when_it_bids_higher():
    mesh, auctioneer = build(providers=[{"ip": "10.0.0.2", "disc_port": 47100}], local_score=95)
    mesh.autoresponders = {"remote": 60}
    assert auctioneer.elect("llm", window=0.5)["node_id"] == "local"


def test_zero_score_keeps_the_local_node_out():
    mesh, auctioneer = build(providers=[{"ip": "10.0.0.2", "disc_port": 47100}], local_score=0)
    mesh.autoresponders = {"remote": 10}
    assert auctioneer.elect("llm", window=0.5)["node_id"] == "remote"


def test_auction_fails_when_nobody_bids():
    mesh, auctioneer = build(providers=[{"ip": "10.0.0.2", "disc_port": 47100}], local_score=0)
    assert auctioneer.elect("llm", window=0.1) is None


def test_hint_travels_with_the_bid_request():
    mesh, auctioneer = build(providers=[{"ip": "10.0.0.2", "disc_port": 47100}], local_score=1)
    auctioneer.elect("llm", hint={"model": "gemma3"}, window=0.05)
    request = [m for _, _, m in mesh.sent if m["type"] == "bid_request"][0]
    assert request["hint"] == {"model": "gemma3"}


def test_bidder_replies_to_the_source_port_not_a_fixed_one():
    """Two nodes on one host share an IP; only the port tells them apart."""
    mesh, auctioneer = build(local_score=50)
    mesh.handlers["bid_request"]({"cap": "llm", "task_id": "t1"}, ("10.0.0.9", 51234))
    ip, port, message = mesh.sent[-1]
    assert (ip, port) == ("10.0.0.9", 51234)
    assert message["type"] == "bid" and message["score"] == 50


def test_bidder_stays_silent_for_capabilities_it_lacks():
    mesh, auctioneer = build(local_score=50, can_serve=False)
    mesh.handlers["bid_request"]({"cap": "tts", "task_id": "t1"}, ("10.0.0.9", 51234))
    assert mesh.sent == []


def test_late_bids_are_ignored():
    """Accepting a bid after the window would mean two nodes think they won."""
    mesh, auctioneer = build(local_score=10)
    auctioneer.elect("llm", window=0.05)
    mesh.handlers["bid"]({"task_id": "stale", "node_id": "ghost", "score": 100}, ("10.0.0.3", 47100))
    assert auctioneer._pending == {}


def test_a_broken_scorer_does_not_break_the_auction():
    mesh = FakeMesh()

    def explode(cap, hint=None):
        raise RuntimeError("sensor on fire")

    auctioneer = Auctioneer(mesh, can_serve=lambda c: True, score_fn=explode)
    assert auctioneer.elect("llm", window=0.05) is None
