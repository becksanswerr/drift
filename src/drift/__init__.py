"""DRIFT — Distributed Routing for Inference and Feature Tasks.

A serverless, peer-to-peer framework that routes whole tasks to the machine
best able to run them, instead of splitting one model across machines.

    from drift import Node

    node = Node(name="workshop")

    @node.capability("summarise", cost=2)
    def summarise(payload):
        return {"text": payload["text"][:280]}

    node.run_forever()
"""

from .auction import Auctioneer
from .mesh import MeshNode
from .node import Capability, Node
from .scoring import ScoreContext, default_scorer, explain

__version__ = "0.3.0"

__all__ = [
    "Node",
    "Capability",
    "MeshNode",
    "Auctioneer",
    "ScoreContext",
    "default_scorer",
    "explain",
    "__version__",
]
