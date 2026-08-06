"""The auction: pick the node that should run a task.

The auctioneer runs on whichever node wants work done. It asks every peer that
advertises the capability for a score, waits a short window, and returns the
highest bidder. There is no coordinator and no shared state — the node that
opened the auction is the only one that needs to agree with itself.

Two things here exist because of specific failures:

- Bids reply to the *source address* of the request, port included. Replying to
  a well-known port breaks the moment two nodes share a host (a laptop running
  a second instance for testing is the normal case, not the exotic one).

- Every bid carries a `why` payload. Without it, "why did this job land on the
  slow machine?" could only be answered by SSHing into each box and reading
  logs. With it, one log line shows the whole table.
"""

from __future__ import annotations

import threading
import time
import uuid

from .callbacks import check_callback

DEFAULT_WINDOW = 1.2


class Auctioneer:
    def __init__(self, mesh, can_serve, score_fn, log=None, bid_detail=None):
        """
        Args:
            mesh: a :class:`drift.mesh.MeshNode`.
            can_serve: ``cap -> bool``, whether this node can run the capability.
            score_fn: ``(cap, hint) -> int``. Return 0 or less to stay out.
            bid_detail: optional ``(cap, hint) -> dict`` explaining the score.
            log: optional ``fn(msg, level=..., source=...)``.
        """
        # Checked here rather than at call time: inside the auction a wrong
        # signature is indistinguishable from "this node chose not to bid", and
        # that ambiguity once hid a completely dead auction. See callbacks.py.
        check_callback(can_serve, "can_serve", "cap")
        check_callback(score_fn, "score_fn", "cap", {})
        check_callback(bid_detail, "bid_detail", "cap", {})
        check_callback(log, "log", "message", level="info", source="drift")

        self.mesh = mesh
        self.can_serve = can_serve
        self.score_fn = score_fn
        self.bid_detail = bid_detail
        self.log = log or (lambda msg, level="info", source=None: None)
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()

        mesh.register_control_handler("bid_request", self._on_bid_request)
        mesh.register_control_handler("bid", self._on_bid)

    # ---------------- bidder side ----------------
    def _on_bid_request(self, msg, addr) -> None:
        cap = msg.get("cap")
        task_id = msg.get("task_id")
        hint = msg.get("hint") or {}
        if not cap or not self.can_serve(cap):
            return
        try:
            score = self.score_fn(cap, hint)
        except Exception as e:
            # `error`, not `warn`: a node that cannot score cannot bid, and a node
            # that cannot bid does no work. That is never routine.
            self.log(f"score function failed for '{cap}': {e}", level="error")
            return
        if score <= 0:
            self.log(f"not bidding on '{cap}' (score={score})", level="debug")
            return

        self.mesh.send_control(
            addr[0],
            addr[1],
            {
                "type": "bid",
                "task_id": task_id,
                "score": score,
                "name": self.mesh.name,
                "http_port": self.mesh.http_port,
                "why": self._detail(cap, hint),
            },
        )
        self.log(f"bid sent: '{cap}' score={score} -> {addr[0]}:{addr[1]}", level="debug")

    def _detail(self, cap, hint):
        if not self.bid_detail:
            return None
        try:
            return self.bid_detail(cap, hint)
        except Exception:
            return None

    # ---------------- auctioneer side ----------------
    def _on_bid(self, msg, addr) -> None:
        task_id = msg.get("task_id")
        with self._lock:
            pending = self._pending.get(task_id)
            if pending is None:
                # Late bid: the window already closed. Dropping it is correct —
                # accepting it would mean two nodes believe they won.
                return
            pending["bids"][msg.get("node_id")] = {
                "score": msg.get("score", 0),
                "ip": addr[0],
                "http_port": msg.get("http_port"),
                "name": msg.get("name"),
                "why": msg.get("why"),
            }

    def elect(self, cap: str, hint: dict | None = None, window: float = DEFAULT_WINDOW):
        """Run an auction for `cap`. Returns the winning bid dict, or None.

        `hint` is a small dict of routing metadata (e.g. ``{"model": "gemma3"}``)
        that travels with the bid request so bidders can score honestly. It is
        deliberately not the payload: the payload can be large or sensitive and
        has no business on the control plane.

        The winner dict carries `node_id`, `name`, `ip`, `http_port`, `score`,
        `why` and the `task_id` the caller should use for the execute call.
        """
        hint = hint or {}
        providers = self.mesh.providers_of(cap)
        local_eligible = self.can_serve(cap)
        if not providers and not local_eligible:
            self.log(f"auction '{cap}': nobody advertises this capability", level="warn")
            return None

        task_id = str(uuid.uuid4())[:8]
        with self._lock:
            self._pending[task_id] = {"bids": {}}
            if local_eligible:
                # The local node bids too, through the same path as everyone
                # else, so "run it here" is a decision the auction makes rather
                # than a shortcut that bypasses it.
                try:
                    local_score = self.score_fn(cap, hint)
                    if local_score > 0:
                        self._pending[task_id]["bids"][self.mesh.node_id] = {
                            "score": local_score,
                            "ip": "127.0.0.1",
                            "http_port": self.mesh.http_port,
                            "name": self.mesh.name,
                            "why": self._detail(cap, hint),
                        }
                except Exception as e:
                    self.log(f"local score function failed for '{cap}': {e}", level="error")

        request = {"type": "bid_request", "task_id": task_id, "cap": cap, "hint": hint}
        for peer in providers:
            self.mesh.send_control(peer["ip"], peer.get("disc_port", self.mesh.discovery_port), request)

        self._collect(task_id, expected=len(providers) + (1 if local_eligible else 0), window=window)

        with self._lock:
            bids = self._pending.pop(task_id, {}).get("bids", {})
        if not bids:
            self.log(
                f"auction '{cap}' failed: no bids from {len(providers)} candidates",
                level="warn",
                source=f"drift/{cap}",
            )
            return None

        winner_id = max(bids, key=lambda k: bids[k]["score"])
        winner = dict(bids[winner_id], node_id=winner_id, task_id=task_id)

        table = " | ".join(
            f"{b.get('name', '?')}={b.get('score', 0)}p"
            + (f"({b['why'].get('model', '?')})" if isinstance(b.get("why"), dict) and "model" in b["why"] else "")
            for b in sorted(bids.values(), key=lambda b: -b.get("score", 0))
        )
        self.log(
            f"auction '{cap}' won by {winner['name']} ({winner['score']}p) · {table}",
            level="success",
            source=f"drift/{cap}",
        )
        return winner

    def _collect(self, task_id: str, expected: int, window: float) -> None:
        """Wait for bids, but stop early once everyone has answered.

        The window is a worst case, not a fixed cost. On a two-node Hive the
        common path finishes in tens of milliseconds instead of always paying
        the full 1.2 seconds.
        """
        deadline = time.time() + window
        while time.time() < deadline:
            with self._lock:
                pending = self._pending.get(task_id)
                if pending is None:
                    return
                if expected and len(pending["bids"]) >= expected:
                    return
            time.sleep(0.02)
