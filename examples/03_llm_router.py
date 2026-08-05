"""Turning a machine with Ollama or LM Studio into an inference node.

    python examples/03_llm_router.py

Run this on every machine that has a local model server. Each one advertises
`llm`, and each bids according to which weights it already has resident — so a
prompt for `gemma3` lands on the machine where gemma3 is already loaded, even
if that machine is busier than its neighbours.

Ask for work from anywhere in the Hive:

    drift call llm --prompt "Explain UDP broadcast to a plumber" --model gemma3
"""

from drift import Node
from drift.llm import LocalLLM

node = Node(name="inference-box", secret=None)  # set a secret on a shared network

llm = LocalLLM(backend="auto")  # tries ollama, then lm studio, then mock
llm.attach(node, cap="llm")

print(f"backend : {llm.backend}")
print(f"resident: {llm.resident or 'nothing loaded yet'}")
print(f"on disk : {len(llm.installed)} model(s)")


@node.capability("models")
def models(payload):
    """A trivial capability so other nodes can inspect this one's catalogue."""
    llm.refresh()
    return {"resident": llm.resident, "installed": llm.installed, "backend": llm.backend}


if __name__ == "__main__":
    node.run_forever()
