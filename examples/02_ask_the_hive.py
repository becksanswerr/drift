"""Asking the Hive for work you cannot do yourself.

This node registers nothing. It joins, waits to hear who is out there, and
delegates. `call` blocks until the winning node returns a result.

    python examples/02_ask_the_hive.py

Run 01_hello_capability.py on this or another machine first.
"""

import time

from drift import Node

node = Node(name="asker")

with node:  # starts discovery + data plane, stops them on exit
    print("listening for peers...")
    time.sleep(4)  # one or two beacon intervals

    for peer in node.peers():
        print(f"  {peer['name']:<16} {peer['ip']:<16} caps={', '.join(peer['caps'])}")

    if not node.peers():
        raise SystemExit("no peers found — is a node running on this network?")

    result = node.call("summarise", {"text": "DRIFT routes tasks, not tensors. " * 20})
    print("\nresult:", result)
