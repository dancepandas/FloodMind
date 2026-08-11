"""Explicit, idempotent Matplotlib configuration for FloodMind plots."""

from __future__ import annotations

from pathlib import Path
import threading
import warnings

_configured = False
_lock = threading.Lock()


def configure_plotting(source_font_dir: str | Path | None = None) -> None:
    """Configure Matplotlib defaults and bundled fonts once per process.

    Matplotlib is imported only when this API is called. Package imports never
    invoke it implicitly.
    """
    global _configured
    if _configured:
        return
    with _lock:
        if _configured:
            return

        import matplotlib
        from matplotlib import font_manager
        from matplotlib.font_manager import FontProperties

        font_dir = Path(source_font_dir) if source_font_dir else Path(__file__).resolve().parents[1] / "source"
        custom_names: list[str] = []
        custom_paths: list[str] = []
        if font_dir.exists():
            for pattern in ("*.ttf", "*.otf", "*.ttc"):
                for font_path in sorted(font_dir.glob(pattern)):
                    try:
                        font_manager.fontManager.addfont(str(font_path))
                        custom_names.append(FontProperties(fname=str(font_path)).get_name())
                        custom_paths.append(str(font_path))
                    except Exception:
                        continue

        serif = [*custom_names, "SimSun", "Noto Serif CJK SC", "Source Han Serif SC", "Songti SC", "Times New Roman"]
        sans = [*custom_names, "WenQuanYi Zen Hei", "WenQuanYi Micro Hei", "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", "Heiti SC", "Arial Unicode MS"]
        available = {font.name: font.fname for font in font_manager.fontManager.ttflist}
        fallback_name = next((name for name in [*serif, *sans] if name in available), None)
        fallback_path = available.get(fallback_name) if fallback_name else (custom_paths[0] if custom_paths else None)

        matplotlib.rcParams.update({
            "axes.unicode_minus": False,
            "font.family": "sans-serif",
            "font.serif": [*serif, "DejaVu Serif"],
            "font.sans-serif": [*sans, "DejaVu Sans", "Arial", "Liberation Sans"],
            "axes.linewidth": 0.8,
            "xtick.direction": "in",
            "ytick.direction": "in",
            "xtick.major.width": 0.8,
            "ytick.major.width": 0.8,
            "xtick.top": True,
            "ytick.right": True,
            "figure.dpi": 300,
            "mathtext.fontset": "stix",
        })

        if fallback_path:
            original_findfont = font_manager.findfont

            def findfont(prop, *args, **kwargs):
                try:
                    families = [prop] if isinstance(prop, str) else list(prop.get_family() or [])
                    if any(name in [*serif, *sans] for name in families):
                        return fallback_path
                except Exception:
                    pass
                return original_findfont(prop, *args, **kwargs)

            font_manager.findfont = findfont
            warnings.filterwarnings("ignore", message=r".*Glyph .* missing from font\(s\).*")

        _configured = True
