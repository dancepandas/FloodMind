"""FloodMind Server — SSE event stream + REST API"""
from floodmind.server.sse_server import _app, run_server

__all__ = ["_app", "run_server"]
