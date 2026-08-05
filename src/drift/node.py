"""`drift.Node` — the object most users touch.

A Node is a machine in the Hive. It advertises capabilities, bids for work it
is suited to, executes what it wins, and can itself ask the Hive to run
something for it. The same object is both worker and client; there is no
separate server process, because there is no server.

    from drift import Node

    node = Node(name="workshop", secret="hunter2")

    @node.capability("summarise", cost=2)
    def summarise(payload):
        return {"text": payload["text"][:280]}

    node.start()
    print(node.call("transcribe", {"path": "a.wav"}))   # runs wherever it fits
    node.run_forever()
"""

from __future__ import annotations

import socket
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .auction import DEFAULT_WINDOW, Auctioneer
from .dataplane import post_json, serve_in_thread
from .mesh import DEFAULT_DISCOVERY_PORT, DEFAULT_HTTP_PORT, MeshNode
from .scoring import ScoreContext, default_scorer, explain

LOG_HISTORY = 200


@dataclass
class Capability:
    name: str
    fn: object
    cost: int = 1
    scorer: object = None  # optional per-capability scorer(ctx) -> int
    detail: object = None  # optional per-capability detail(ctx) -> dict


def local_ip() -> str:
    """Best-effort outward-facing address of this machine.

    No packet is sent; connecting a UDP socket only picks a route. This is the
    address peers must reach us on, which is not necessarily the one
    `gethostname` resolves to on a multi-homed box.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class Node:
    def __init__(
        self,
        name: str | None = None,
        *,
        discovery: str = "lan",
        seeds=None,
        discovery_port: int = DEFAULT_DISCOVERY_PORT,
        http_port: int = DEFAULT_HTTP_PORT,
        secret: str | None = None,
        scorer=None,
        on_log=None,
        max_workers: int = 4,
        advertise_ip: str | None = None,
        bind_host: str = "0.0.0.0",
    ):
        self.bind_host = bind_host
        self.capabilities: dict[str, Capability] = {}
        self.scorer = scorer or default_scorer
        self.logs: deque = deque(maxlen=LOG_HISTORY)
        self._on_log = on_log
        self._active = 0
        self._active_lock = threading.Lock()
        self._waiters: dict[str, dict] = {}
        self._waiters_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="drift-task")
        self._http = None
        self._started = False

        self.mesh = MeshNode(
            name=name,
            caps=[],
            discovery=discovery,
            seeds=seeds,
            discovery_port=discovery_port,
            http_port=http_port,
            secret=secret,
            on_log=self.log,
        )
        self.auctioneer = Auctioneer(
            self.mesh,
            can_serve=self.can_serve,
            score_fn=self.score,
            log=self.log,
            bid_detail=self.bid_detail,
        )
        self.advertise_ip = advertise_ip or local_ip()

    # ---------------- identity ----------------
    @property
    def name(self) -> str:
        return self.mesh.name

    @property
    def node_id(self) -> str:
        return self.mesh.node_id

    @property
    def http_port(self) -> int:
        return self.mesh.http_port

    # ---------------- logging ----------------
    def log(self, msg: str, level: str = "info", source: str | None = None) -> None:
        entry = {"ts": time.time(), "level": level, "source": source or self.name, "msg": msg}
        self.logs.append(entry)
        if self._on_log:
            self._on_log(entry)

    # ---------------- capability registration ----------------
    def capability(self, name: str, cost: int = 1, scorer=None, detail=None):
        """Decorator registering a handler for `name`.

        `cost` is a hint about how heavy the capability is (1 = trivial,
        3 = expensive); it slightly lowers the bid so a node prefers to hand
        heavy work to an idler. `scorer` overrides the node scorer for this
        capability alone — that is the hook :mod:`drift.llm` uses.
        """

        def decorator(fn):
            self.register(name, fn, cost=cost, scorer=scorer, detail=detail)
            return fn

        return decorator

    def register(self, name: str, fn, cost: int = 1, scorer=None, detail=None) -> None:
        self.capabilities[name] = Capability(name=name, fn=fn, cost=cost, scorer=scorer, detail=detail)
        self.mesh.caps.add(name)
        self.log(f"capability registered: {name} (cost={cost})", level="debug")

    def can_serve(self, cap: str) -> bool:
        return cap in self.capabilities

    # ---------------- scoring ----------------
    def _context(self, cap: str, hint: dict | None = None) -> ScoreContext:
        capability = self.capabilities.get(cap)
        with self._active_lock:
            active = self._active
        return ScoreContext(
            cap=cap,
            metrics=self.mesh.metrics(),
            active_tasks=active,
            cost=capability.cost if capability else 1,
            caps=set(self.capabilities),
            extra=dict(hint or {}),
        )

    def score(self, cap: str, hint: dict | None = None) -> int:
        capability = self.capabilities.get(cap)
        if capability is None:
            return 0
        scorer = capability.scorer or self.scorer
        return int(scorer(self._context(cap, hint)))

    def bid_detail(self, cap: str, hint: dict | None = None) -> dict:
        capability = self.capabilities.get(cap)
        ctx = self._context(cap, hint)
        if capability is not None and capability.detail:
            return capability.detail(ctx)
        return explain(ctx)

    # ---------------- introspection ----------------
    def info(self) -> dict:
        return dict(self.mesh.info(), capabilities={c.name: c.cost for c in self.capabilities.values()})

    def peers(self) -> list[dict]:
        return list(self.mesh.peers_snapshot().values())

    def state(self) -> dict:
        return {
            "info": self.info(),
            "peers": self.peers(),
            "logs": list(self.logs)[-60:],
            "active_tasks": self._active,
        }

    # ---------------- execution ----------------
    def _run(self, cap: str, payload: dict):
        capability = self.capabilities[cap]
        with self._active_lock:
            self._active += 1
        try:
            return capability.fn(payload)
        finally:
            with self._active_lock:
                self._active -= 1

    def _accept_remote_task(self, task_id: str, cap: str, payload: dict, reply_to: dict) -> None:
        self._executor.submit(self._run_remote_task, task_id, cap, payload, reply_to)

    def _run_remote_task(self, task_id: str, cap: str, payload: dict, reply_to: dict) -> None:
        try:
            if not self.can_serve(cap):
                result = {"error": f"'{cap}' is not available on {self.name}"}
            else:
                self.log(f"executing remote task {task_id} ({cap})")
                result = self._run(cap, payload)
        except Exception as e:
            result = {"error": f"{type(e).__name__}: {e}"}
            self.log(f"task {task_id} ({cap}) failed: {e}", level="error")

        ip, port = reply_to.get("ip"), reply_to.get("http_port")
        if not ip or not port:
            return
        try:
            post_json(
                f"http://{ip}:{port}/drift/result",
                {"task_id": task_id, "result": result},
                headers=self.mesh.security.auth_header(),
                timeout=15,
            )
        except Exception as e:
            # The requester will time out. Log loudly: a result computed and
            # then lost is the most expensive failure in the system.
            self.log(f"could not return result for {task_id}: {e}", level="error")

    def _deliver_result(self, task_id, result) -> None:
        with self._waiters_lock:
            waiter = self._waiters.get(task_id)
        if waiter:
            waiter["result"] = result
            waiter["event"].set()

    # ---------------- the public call ----------------
    def call(self, cap: str, payload: dict | None = None, timeout: float = 60.0,
             prefer_remote: bool = False, hint: dict | None = None,
             window: float = DEFAULT_WINDOW):
        """Run `cap` somewhere in the Hive and return its result.

        By default a capability that exists locally is executed locally without
        an auction — putting a local function call on the network is absurd for
        cheap work.

        `prefer_remote=True` opens the auction anyway. Use it when the real
        cost is warm state rather than compute: a model already resident in a
        neighbour's VRAM beats loading it here, and a 1.2s auction is cheap
        against a 40s model load. The local node bids too, so if it really is
        the best choice it still wins and nothing is lost.
        """
        payload = payload or {}
        if self.can_serve(cap) and not prefer_remote:
            return self._run(cap, payload)

        winner = self.auctioneer.elect(cap, hint=hint, window=window)
        if not winner:
            return {"error": f"no node provides '{cap}'"}

        if winner["node_id"] == self.node_id:
            return self._run(cap, payload)

        task_id = winner["task_id"]
        event = threading.Event()
        with self._waiters_lock:
            self._waiters[task_id] = {"event": event, "result": None}

        try:
            post_json(
                f"http://{winner['ip']}:{winner['http_port']}/drift/execute",
                {
                    "task_id": task_id,
                    "cap": cap,
                    "payload": payload,
                    "reply_to": {"ip": self.advertise_ip, "http_port": self.http_port},
                },
                headers=self.mesh.security.auth_header(),
                timeout=10,
            )
        except Exception as e:
            with self._waiters_lock:
                self._waiters.pop(task_id, None)
            self.log(f"could not reach winner {winner['name']}: {e}", level="error")
            return {"error": f"winner unreachable: {e}"}

        delivered = event.wait(timeout)
        with self._waiters_lock:
            waiter = self._waiters.pop(task_id, None)
        if not delivered:
            self.log(f"call('{cap}') timed out after {timeout}s on {winner['name']}", level="warn")
            return {"error": "timeout"}
        return waiter["result"]

    # ---------------- lifecycle ----------------
    def start(self) -> "Node":
        if self._started:
            return self
        self.mesh.start()
        try:
            self._http, _ = serve_in_thread(self, self.http_port, self.bind_host)
        except OSError as e:
            self.mesh.stop()
            raise RuntimeError(
                f"HTTP port {self.http_port} is already in use. "
                f"Pass http_port=... to run a second node on this machine."
            ) from e
        self.log(f"data plane listening on {self.advertise_ip}:{self.http_port}", level="success")
        self._started = True
        return self

    def stop(self) -> None:
        if self._http:
            self._http.shutdown()
            self._http.server_close()
            self._http = None
        self.mesh.stop()
        self._executor.shutdown(wait=False)
        self._started = False

    def run_forever(self) -> None:
        if not self._started:
            self.start()
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.log("shutting down", level="warn")
        finally:
            self.stop()

    # context manager: `with Node(...) as node:`
    def __enter__(self) -> "Node":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
