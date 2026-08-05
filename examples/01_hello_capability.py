"""The smallest useful node: one machine offering one capability.

    python examples/01_hello_capability.py

Then, from any other machine on the same network:

    drift call summarise --json '{"text": "..."}'
"""

from drift import Node

node = Node(name="hello-node")


@node.capability("summarise", cost=2)
def summarise(payload):
    """Handlers take a dict and return anything JSON-serialisable."""
    text = payload.get("text", "")
    return {"summary": text[:280], "chars": len(text)}


@node.capability("reverse")
def reverse(payload):
    return {"text": payload.get("text", "")[::-1]}


if __name__ == "__main__":
    print("advertising:", ", ".join(node.capabilities))
    print("press Ctrl+C to leave the Hive")
    node.run_forever()
