"""Terminal dashboard for a running node.

Optional by design: `rich` is an extra, and everything here degrades to plain
text without it. A framework that cannot start because a display library is
missing has its priorities backwards.
"""

from __future__ import annotations

import time

LEVEL_STYLE = {
    "success": "green",
    "warn": "yellow",
    "error": "bold red",
    "debug": "dim",
    "info": "white",
}


def _have_rich() -> bool:
    try:
        import rich  # noqa: F401

        return True
    except ImportError:
        return False


def run(node, refresh: float = 4.0) -> None:
    """Block, rendering `node`'s state until interrupted."""
    if _have_rich():
        _run_rich(node, refresh)
    else:
        _run_plain(node)


def _run_rich(node, refresh: float) -> None:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    console = Console()

    def peers_panel():
        table = Table(expand=True, box=None)
        for column, justify in (("node", "left"), ("address", "left"), ("caps", "left"),
                                ("cpu", "right"), ("ram", "right"), ("gpu", "right")):
            table.add_column(column, justify=justify)

        info = node.info()
        rows = [(f"{info['name']} (you)", f"{node.advertise_ip}:{info['http_port']}",
                 ", ".join(info["caps"]) or "-", info["metrics"])]
        for peer in sorted(node.peers(), key=lambda p: p["name"]):
            rows.append((peer["name"], f"{peer['ip']}:{peer.get('http_port', '-')}",
                         ", ".join(peer.get("caps", [])) or "-", peer.get("metrics", {})))

        for name, address, caps, metrics in rows:
            gpu = metrics.get("gpu")
            table.add_row(name, address, caps,
                          f"{metrics.get('cpu', '-')}%",
                          f"{metrics.get('ram', '-')}%",
                          "-" if gpu is None else f"{gpu}%")
        return Panel(table, title=f"Hive · {len(rows)} node(s)", border_style="cyan")

    def logs_panel():
        text = Text()
        for entry in list(node.logs)[-18:]:
            stamp = time.strftime("%H:%M:%S", time.localtime(entry["ts"]))
            text.append(f"{stamp} ", style="dim")
            text.append(f"{entry['source']:<14.14} ", style="cyan")
            text.append(f"{entry['msg']}\n", style=LEVEL_STYLE.get(entry["level"], "white"))
        return Panel(text, title="activity", border_style="yellow")

    def frame():
        info = node.info()
        header = Panel(
            Text.from_markup(
                f"[bold cyan]DRIFT[/bold cyan] {info['name']} · id={info['node_id']} · "
                f"caps=[yellow]{', '.join(info['caps']) or 'none'}[/yellow] · "
                f"discovery={info['discovery']} · secured={'yes' if info['secured'] else 'no'} · "
                f"active={node.state()['active_tasks']}"
            ),
            border_style="blue",
        )
        return Group(header, peers_panel(), logs_panel())

    with Live(frame(), console=console, refresh_per_second=refresh, screen=True) as live:
        try:
            while True:
                time.sleep(1.0 / refresh)
                live.update(frame())
        except KeyboardInterrupt:
            pass


def _run_plain(node) -> None:
    seen = 0
    try:
        while True:
            entries = list(node.logs)
            for entry in entries[seen:]:
                stamp = time.strftime("%H:%M:%S", time.localtime(entry["ts"]))
                print(f"{stamp} [{entry['level']:<7}] {entry['source']}: {entry['msg']}", flush=True)
            seen = len(entries)
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
