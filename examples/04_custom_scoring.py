"""Teaching a node what it is good at.

The default scorer knows about CPU, RAM, GPU and how many tasks a node is
already running. It cannot know that this particular machine has the fast SSD,
or that it is the one in the bedroom and should stay quiet at night. That is
what a custom scorer is for.

Return 0 to drop out of an auction entirely; higher wins. Scores are ordinal —
only the ordering matters, so returning values above 100 is allowed.
"""

import datetime

from drift import Node
from drift.scoring import default_scorer

node = Node(name="bedroom-pc")


def quiet_at_night(ctx):
    """This machine sits next to a bed. Its fans are not a shared resource."""
    hour = datetime.datetime.now().hour
    if 23 <= hour or hour < 8:
        return 0  # refuse work overnight; some other node will take it
    return default_scorer(ctx)


node.scorer = quiet_at_night


def prefers_big_files(ctx):
    """Per-capability scorer: this box has the NVMe, bid hard for disk work."""
    size_mb = ctx.extra.get("size_mb", 0)
    return default_scorer(ctx) + (30 if size_mb > 500 else 0)


@node.capability("transcode", cost=3, scorer=prefers_big_files)
def transcode(payload):
    return {"ok": True, "path": payload.get("path")}


if __name__ == "__main__":
    # The hint is what reaches the bidders; the payload stays off the control
    # plane until a winner exists.
    #   node.call("transcode", {"path": "a.mkv"}, hint={"size_mb": 1400})
    node.run_forever()
