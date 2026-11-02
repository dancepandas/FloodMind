"""Opt-in, local snapshots of model SDK arguments (never HTTP headers).

Wrap a ModelClient's transport after construction and before running an agent.
Files contain full prompts and tool results: treat them as sensitive diagnostics.
"""

import json
from pathlib import Path


class RequestCaptureTransport:
    """Record each send without changing the wrapped transport's behavior."""

    def __init__(self, transport, path):
        self.transport = transport
        self.path = Path(path)
        # Exclusive creation protects existing experimental evidence.
        with self.path.open("x", encoding="utf-8"):
            pass
        self.sequence = 0

    def send(self, request):
        # SDK transport options can contain credentials; they are not body fields.
        body = {key: value for key, value in request.items()
                if key not in {"extra_headers", "extra_query", "timeout"}}
        sequence = self.sequence + 1
        snapshot = json.dumps({"sequence": sequence, "representation": "sdk_arguments",
                               "request": body}, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(snapshot + "\n")
        self.sequence = sequence
        return self.transport.send(request)

    def classify_error(self, exc):
        return self.transport.classify_error(exc)
