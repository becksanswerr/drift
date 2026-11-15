"""Contract tests locking the exact Drift API surface used by Valori (core/runtime.py)."""

import pytest

from drift.auction import Auctioneer
from drift.mesh import MeshNode


def test_mesh_node_exposes_valori_attributes():
    logs = []
    mesh = MeshNode(
        name="valori-contract-node",
        http_port=0,
        secret="contract-secret",
        on_log=lambda message, level="info", source="drift": logs.append((source, level, message)),
    )
    assert mesh.name == "valori-contract-node"
    assert hasattr(mesh, "node_id")
    assert hasattr(mesh, "http_port")
    assert hasattr(mesh, "peers")


def test_auctioneer_enforces_score_fn_two_arg_signature_at_wiring_time():
    mesh = MeshNode(name="contract-auction-node", http_port=0)

    # Valid Valori signature: score_fn(cap, hint)
    auct = Auctioneer(
        mesh,
        can_serve=lambda cap: cap == "worker",
        score_fn=lambda cap, hint: 90,
        log=lambda message, level="info", source="drift": None,
    )
    assert auct.score_fn("worker", {"model": "gemma-4"}) == 90

    # Broken 1-arg signature must fail loudly at wiring time (lesson from 34f1f99)
    with pytest.raises(TypeError):
        Auctioneer(
            mesh,
            can_serve=lambda cap: True,
            score_fn=lambda cap: 90,
        )
