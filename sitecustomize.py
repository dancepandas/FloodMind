"""Legacy compatibility shim for explicit plotting configuration.

This module is intentionally inert when Python imports ``sitecustomize``.
It is no longer included in FloodMind wheels; callers should import
``configure_plotting`` from :mod:`floodmind.plotting` directly.
"""

from __future__ import annotations


def configure_plotting(*args, **kwargs) -> None:
    """Delegate to :func:`floodmind.plotting.configure_plotting` lazily."""
    from floodmind.plotting import configure_plotting as configure

    configure(*args, **kwargs)


# Historical private name retained for source-tree callers. Nothing invokes it
# automatically because interpreter startup must remain side-effect free.
_patch_matplotlib = configure_plotting
