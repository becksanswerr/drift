"""The mesh core: discovery, the peer table, and control-message routing.

Design decisions, and the reasons behind them:

- **Hybrid transport.** Discovery is UDP; control messages (bid requests, bids)
  are UDP unicast; task payloads and results go over HTTP. Broadcast is fine
  for "who is out there" and useless for anything you need to arrive.

- **Pluggable discovery.** `discovery="lan"` broadcasts. `discovery="static"`
  sends beacons to a fixed seed list. The static mode is not a nicety:
  Tailscale is L3 WireGuard and has no broadcast domain, so a mesh that only
  knows how to broadcast can never span two houses.

- **One discovery port.** Earlier versions sprayed beacons across ports
  8000-8009 hoping one would land. Every node then heard every packet up to
  ten times and the peer table flapped. One port, unicast replies to the
  source address.

- **One node per machine, many capabilities.** A machine advertises a set of
  caps (`llm`, `tts`, `vision`, ...). Running one process per capability was
  the old model and it multiplied beacons for no gain.

- **The listener never does work.** The receive loop only parses and
  dispatches. Any handler that blocks stalls discovery for every peer, and a
  stalled beacon looks exactly like a dead machine.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import uuid

from .security import NetworkSecurity

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

DEFAULT_DISCOVERY_PORT = 47100
DEFAULT_HTTP_PORT = 8700
BEACON_INTERVAL = 2.0
PEER_TIMEOUT = 12.0  # a peer silent for this long is dropped from the table

# GPU load is read through nvidia-smi (via GPUtil), which is a subprocess call
# and can take one to two seconds when cold. Bids are collected in a ~1.2s
# window, so calling it synchronously on the hot path means the bid arrives
# after the auction closed — which shows up as "no bids received" and the task
# silently staying local. So: sample in the background, serve from cache.
_gpu_state = {"value": None, "sampled_at": 0.0}
_gpu_lock = threading.Lock()
_GPU_TTL = 4.0


def _sample_gpu() -> None:
    value = None
    try:
        import GPUtil

        gpus = GPUtil.getGPUs()
        if gpus:
            value = int(gpus[0].load * 100)
    except Exception:
        value = None
    with _gpu_lock:
        _gpu_state["value"] = value
        _gpu_state["sampled_at"] = time.time()


def gpu_load() -> int | None:
    """Return the cached GPU load percentage, refreshing in the background."""
    now = time.time()
    with _gpu_lock:
        value, sampled_at = _gpu_state["value"], _gpu_state["sampled_at"]
    if now - sampled_at > _GPU_TTL:
        with _gpu_lock:
            # Bump the timestamp before spawning so a burst of callers does not
            # start a thread each.
            _gpu_state["sampled_at"] = now
        threading.Thread(target=_sample_gpu, daemon=True).start()
    return value


class MeshNode:
    """Discovery, peer table, and unicast control messaging.

    This class knows nothing about tasks. It is the transport that
    :class:`drift.auction.Auctioneer` and :class:`drift.node.Node` sit on.
    """

    def __init__(
        self,
        name: str | None = None,
        caps=None,
        *,
        discovery: str = "lan",
        seeds=None,
        discovery_port: int = DEFAULT_DISCOVERY_PORT,
        http_port: int = DEFAULT_HTTP_PORT,
        secret: str | None = None,
        on_log=None,
        on_peer_change=None,
    ):
        self.node_id = str(uuid.uuid4())[:8]
        self.name = name or f"node-{self.node_id}"
        self.caps = set(caps or [])
        self.discovery = discovery
        self.seeds = list(seeds or [])
        self.discovery_port = discovery_port
        self.http_port = http_port
        self.security = NetworkSecurity(secret)
        self.on_log = on_log or (lambda msg, level="info", source=None: None)
        self.on_peer_change = on_peer_change

        self.peers: dict[str, dict] = {}
        self._peers_lock = threading.Lock()
        self._control_handlers = {}
        self.running = False
        self._threads: list[threading.Thread] = []

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:  # pragma: no cover - platform dependent
                pass
        self.sock.bind(("", self.discovery_port))

    # ---------------- logging ----------------
    def log(self, msg: str, level: str = "info", source: str | None = None) -> None:
        self.on_log(msg, level=level, source=source or self.name)

    # ---------------- metrics ----------------
    def metrics(self) -> dict:
        cpu = int(psutil.cpu_percent(interval=None)) if psutil else 0
        ram = int(psutil.virtual_memory().percent) if psutil else 0
        return {"cpu": cpu, "ram": ram, "gpu": gpu_load()}

    # ---------------- addressing ----------------
    def _seed_targets(self) -> list[tuple[str, int]]:
        targets = []
        for seed in self.seeds:
            if isinstance(seed, (tuple, list)):
                targets.append((seed[0], int(seed[1])))
            elif isinstance(seed, str) and ":" in seed:
                host, port = seed.rsplit(":", 1)
                targets.append((host, int(port)))
            elif isinstance(seed, str):
                targets.append((seed, self.discovery_port))
        return targets

    def _beacon_targets(self) -> list[tuple[str, int]]:
        if self.discovery == "static":
            return self._seed_targets()
        return [("<broadcast>", self.discovery_port)]

    def _send_raw(self, message: dict, targets) -> None:
        try:
            data = self.security.encrypt(json.dumps(message))
        except (TypeError, ValueError) as e:
            self.log(f"message could not be serialised: {e}", level="error")
            return
        for host, port in targets:
            try:
                self.sock.sendto(data, (host, port))
            except OSError:
                # A single unreachable peer must not abort the rest of the fan-out.
                pass

    # ---------------- control plane ----------------
    def register_control_handler(self, msg_type: str, fn) -> None:
        """Attach a handler for a control message type (`fn(message, addr)`)."""
        self._control_handlers[msg_type] = fn

    def send_control(self, ip: str, port: int, message: dict) -> None:
        message = dict(message)
        message.setdefault("node_id", self.node_id)
        self._send_raw(message, [(ip, int(port))])

    def broadcast_control(self, message: dict) -> None:
        """Unicast to every known peer — works on LAN and over Tailscale alike."""
        message = dict(message)
        message.setdefault("node_id", self.node_id)
        with self._peers_lock:
            targets = [(p["ip"], p.get("disc_port", self.discovery_port)) for p in self.peers.values()]
        self._send_raw(message, targets)

    # ---------------- loops ----------------
    def _beacon_loop(self) -> None:
        while self.running:
            self._send_raw(
                {
                    "type": "beacon",
                    "node_id": self.node_id,
                    "name": self.name,
                    "caps": sorted(self.caps),
                    "http_port": self.http_port,
                    "disc_port": self.discovery_port,
                    "metrics": self.metrics(),
                },
                self._beacon_targets(),
            )
            time.sleep(BEACON_INTERVAL)

    def _listen_loop(self) -> None:
        self.sock.settimeout(1.0)
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                self._prune_peers()
                continue
            except OSError:
                continue

            decrypted = self.security.decrypt(data)
            if not decrypted:
                continue
            try:
                message = json.loads(decrypted)
            except ValueError:
                continue
            if not isinstance(message, dict) or message.get("node_id") == self.node_id:
                continue

            mtype = message.get("type")
            if mtype == "beacon":
                self._on_beacon(message, addr)
            elif mtype in self._control_handlers:
                try:
                    self._control_handlers[mtype](message, addr)
                except Exception as e:  # a broken handler must not kill discovery
                    self.log(f"control handler '{mtype}' failed: {e}", level="error")

    def _on_beacon(self, message: dict, addr) -> None:
        node_id = message.get("node_id")
        if not node_id:
            return
        is_new = node_id not in self.peers
        with self._peers_lock:
            self.peers[node_id] = {
                "node_id": node_id,
                "name": message.get("name", node_id),
                "ip": addr[0],
                "caps": message.get("caps", []),
                "http_port": message.get("http_port"),
                "disc_port": message.get("disc_port", self.discovery_port),
                "metrics": message.get("metrics", {}),
                "last_seen": time.time(),
            }
        if is_new:
            self.log(
                f"peer joined: {message.get('name')} @ {addr[0]} caps={message.get('caps')}",
                level="success",
            )
            if self.on_peer_change:
                self.on_peer_change(self.peers_snapshot())

    def _prune_peers(self) -> None:
        now = time.time()
        dropped = []
        with self._peers_lock:
            for node_id, info in list(self.peers.items()):
                if now - info["last_seen"] > PEER_TIMEOUT:
                    dropped.append(info["name"])
                    del self.peers[node_id]
        for name in dropped:
            self.log(f"peer lost: {name}", level="warn")
        if dropped and self.on_peer_change:
            self.on_peer_change(self.peers_snapshot())

    # ---------------- queries ----------------
    def peers_snapshot(self) -> dict:
        with self._peers_lock:
            return {nid: dict(info) for nid, info in self.peers.items()}

    def providers_of(self, cap: str) -> list[dict]:
        """Peers advertising `cap` (or the wildcard `*`), excluding this node."""
        with self._peers_lock:
            return [
                dict(info)
                for info in self.peers.values()
                if cap in info.get("caps", []) or "*" in info.get("caps", [])
            ]

    def info(self) -> dict:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "caps": sorted(self.caps),
            "discovery": self.discovery,
            "discovery_port": self.discovery_port,
            "http_port": self.http_port,
            "secured": self.security.enabled,
            "metrics": self.metrics(),
        }

    # ---------------- lifecycle ----------------
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        for target in (self._listen_loop, self._beacon_loop):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self._threads.append(thread)
        mode = "LAN broadcast" if self.discovery == "lan" else f"static ({len(self.seeds)} seeds)"
        self.log(
            f"mesh up · id={self.node_id} · caps={sorted(self.caps)} · discovery={mode} · port={self.discovery_port}",
            level="success",
        )

    def stop(self) -> None:
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass
