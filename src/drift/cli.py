"""`drift` command line.

    drift up --caps llm --llm ollama --secret hunter2
    drift call llm --prompt "Write a haiku about idle GPUs" --model gemma3
    drift peers
    drift top

`call` and `peers` do not join the Hive themselves — they ask the node already
running on this machine over its HTTP data plane. One process per machine owns
the discovery socket; everything else borrows it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .dataplane import get_json, post_json
from .mesh import DEFAULT_DISCOVERY_PORT, DEFAULT_HTTP_PORT
from .node import Node
from .security import NetworkSecurity


def _secret(args) -> str | None:
    return args.secret or os.environ.get("DRIFT_SECRET")


def _endpoint(args) -> str:
    return (args.node or os.environ.get("DRIFT_NODE") or f"http://127.0.0.1:{DEFAULT_HTTP_PORT}").rstrip("/")


def _headers(args) -> dict:
    return NetworkSecurity(_secret(args)).auth_header()


# ---------------------------------------------------------------- up
def cmd_up(args) -> int:
    node = Node(
        name=args.name,
        discovery=args.discovery,
        seeds=args.seed,
        discovery_port=args.port,
        http_port=args.http_port,
        secret=_secret(args),
        on_log=None if args.tui else _print_log,
    )

    for spec in args.caps or []:
        name, _, cost = spec.partition(":")
        node.register(name, _echo_handler(name), cost=int(cost or 1))

    if args.llm:
        from .llm import LocalLLM

        LocalLLM(backend=args.llm, url=args.llm_url).attach(node, cap=args.llm_cap)

    if not node.capabilities:
        print("warning: this node advertises no capabilities; it can request work but not do any.",
              file=sys.stderr)

    node.start()
    if args.tui:
        from . import tui

        tui.run(node)
        node.stop()
    else:
        node.run_forever()
    return 0


def _echo_handler(name: str):
    """Placeholder handler for caps declared on the CLI.

    Declaring a capability from the command line is useful for testing routing
    without writing a script, so it answers rather than failing — but it says
    plainly that nothing real is wired up.
    """

    def handler(payload):
        return {"echo": payload, "cap": name,
                "note": "no handler bound; register one in Python to do real work"}

    return handler


def _print_log(entry) -> None:
    print(f"[{entry['level']:<7}] {entry['source']}: {entry['msg']}", flush=True)


# ---------------------------------------------------------------- call
def cmd_call(args) -> int:
    if args.json:
        try:
            payload = json.loads(args.json)
        except ValueError as e:
            print(f"--json is not valid JSON: {e}", file=sys.stderr)
            return 2
    else:
        payload = {}
    if args.prompt:
        payload.setdefault("prompt", args.prompt)
    if args.model:
        payload.setdefault("model", args.model)

    hint = {"model": args.model} if args.model else {}
    body = {"cap": args.cap, "payload": payload, "timeout": args.timeout,
            "prefer_remote": args.remote, "hint": hint}

    try:
        response = post_json(f"{_endpoint(args)}/drift/call", body,
                             headers=_headers(args), timeout=args.timeout + 15)
    except Exception as e:
        print(f"could not reach a node at {_endpoint(args)}: {e}", file=sys.stderr)
        print("start one with:  drift up --caps ...", file=sys.stderr)
        return 1

    result = response.get("result")
    if isinstance(result, dict) and "error" in result:
        print(f"error: {result['error']}", file=sys.stderr)
        return 1
    if isinstance(result, dict) and "text" in result:
        print(result["text"])
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- peers
def cmd_peers(args) -> int:
    try:
        state = get_json(f"{_endpoint(args)}/drift/state", headers=_headers(args))
    except Exception as e:
        print(f"could not reach a node at {_endpoint(args)}: {e}", file=sys.stderr)
        return 1

    info = state["info"]
    print(f"{info['name']} (this node) · id={info['node_id']} · caps={', '.join(info['caps']) or '-'}")
    peers = state.get("peers", [])
    if not peers:
        print("no peers seen yet")
        return 0
    print(f"\n{'node':<18}{'address':<24}{'cpu':>5}{'ram':>5}{'gpu':>6}  caps")
    for peer in sorted(peers, key=lambda p: p["name"]):
        metrics = peer.get("metrics", {})
        gpu = metrics.get("gpu")
        print(f"{peer['name']:<18}{peer['ip'] + ':' + str(peer.get('http_port', '-')):<24}"
              f"{str(metrics.get('cpu', '-')):>5}{str(metrics.get('ram', '-')):>5}"
              f"{('-' if gpu is None else str(gpu)):>6}  {', '.join(peer.get('caps', []))}")
    return 0


# ---------------------------------------------------------------- top
def cmd_top(args) -> int:
    """Attach a read-only dashboard to a running node."""
    from . import tui

    endpoint, headers = _endpoint(args), _headers(args)

    class _RemoteView:
        """Duck-types the parts of Node that the TUI reads."""

        advertise_ip = endpoint.split("//")[-1].split(":")[0]

        def __init__(self):
            self.logs = []
            self._state = {"info": {}, "peers": [], "logs": [], "active_tasks": 0}

        def _refresh(self):
            self._state = get_json(f"{endpoint}/drift/state", headers=headers)
            self.logs = self._state.get("logs", [])

        def info(self):
            self._refresh()
            return self._state["info"]

        def peers(self):
            return self._state.get("peers", [])

        def state(self):
            return self._state

    try:
        view = _RemoteView()
        view._refresh()
    except Exception as e:
        print(f"could not reach a node at {endpoint}: {e}", file=sys.stderr)
        return 1
    tui.run(view, refresh=1.0)
    return 0


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="drift", description="Distributed Routing for Inference and Feature Tasks")
    parser.add_argument("--version", action="version", version=f"drift {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="run a node")
    up.add_argument("--name", help="node name (default: node-<id>)")
    up.add_argument("--caps", nargs="*", metavar="CAP[:COST]", help="capabilities to advertise")
    up.add_argument("--discovery", choices=["lan", "static"], default="lan",
                    help="lan = UDP broadcast; static = seed list (use over Tailscale/WireGuard)")
    up.add_argument("--seed", action="append", metavar="HOST[:PORT]", help="seed peer for static discovery")
    up.add_argument("--port", type=int, default=DEFAULT_DISCOVERY_PORT, help="discovery port")
    up.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT, help="data plane port")
    up.add_argument("--secret", help="shared secret (or set DRIFT_SECRET)")
    up.add_argument("--llm", choices=["auto", "ollama", "lmstudio", "mock"], help="attach a local LLM backend")
    up.add_argument("--llm-url", help="override the backend URL")
    up.add_argument("--llm-cap", default="llm", help="capability name for the LLM (default: llm)")
    up.add_argument("--tui", action="store_true", help="full-screen dashboard instead of log lines")
    up.set_defaults(func=cmd_up)

    call = sub.add_parser("call", help="run a capability somewhere in the Hive")
    call.add_argument("cap")
    call.add_argument("--prompt", help="shorthand for --json '{\"prompt\": ...}'")
    call.add_argument("--json", help="payload as JSON")
    call.add_argument("--model", help="model hint for llm-style capabilities")
    call.add_argument("--timeout", type=float, default=120.0)
    call.add_argument("--remote", action="store_true", help="auction even if this node could serve it locally")
    call.add_argument("--node", help="data plane URL (or set DRIFT_NODE)")
    call.add_argument("--secret", help="shared secret (or set DRIFT_SECRET)")
    call.set_defaults(func=cmd_call)

    peers = sub.add_parser("peers", help="list nodes in the Hive")
    peers.add_argument("--node", help="data plane URL (or set DRIFT_NODE)")
    peers.add_argument("--secret", help="shared secret (or set DRIFT_SECRET)")
    peers.set_defaults(func=cmd_peers)

    top = sub.add_parser("top", help="live dashboard for a running node")
    top.add_argument("--node", help="data plane URL (or set DRIFT_NODE)")
    top.add_argument("--secret", help="shared secret (or set DRIFT_SECRET)")
    top.set_defaults(func=cmd_top)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
