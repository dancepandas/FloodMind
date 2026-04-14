"""Project-wide Python startup tweaks for plotting commands.

Loaded automatically by Python when running from the repository root.
"""

from __future__ import annotations

from pathlib import Path
import warnings


def _patch_matplotlib() -> None:
    try:
        import matplotlib
        from matplotlib import font_manager
        from matplotlib.font_manager import FontProperties
    except Exception:
        return

    source_font_dir = Path(__file__).resolve().parent / 'source'
    custom_font_names: list[str] = []
    custom_font_paths: list[str] = []
    if source_font_dir.exists():
        for pattern in ('*.ttf', '*.otf', '*.ttc'):
            for font_path in sorted(source_font_dir.glob(pattern)):
                try:
                    font_manager.fontManager.addfont(str(font_path))
                    custom_font_names.append(FontProperties(fname=str(font_path)).get_name())
                    custom_font_paths.append(str(font_path))
                except Exception:
                    continue

    preferred_serif_fonts = [
        *custom_font_names,
        'SimSun',
        'Noto Serif CJK SC',
        'Source Han Serif SC',
        'Songti SC',
        'Times New Roman',
    ]
    preferred_sans_fonts = [
        *custom_font_names,
        'WenQuanYi Zen Hei',
        'WenQuanYi Micro Hei',
        'Microsoft YaHei',
        'SimHei',
        'Noto Sans CJK SC',
        'Source Han Sans SC',
        'PingFang SC',
        'Heiti SC',
        'Arial Unicode MS',
    ]

    available_fonts = {font.name: font.fname for font in font_manager.fontManager.ttflist}
    fallback_name = next(
        (name for name in [*preferred_serif_fonts, *preferred_sans_fonts] if name in available_fonts),
        None,
    )
    fallback_path = available_fonts.get(fallback_name) if fallback_name else None
    if not fallback_path and custom_font_paths:
        fallback_path = custom_font_paths[0]

    matplotlib.rcParams['axes.unicode_minus'] = False
    matplotlib.rcParams['font.family'] = 'sans-serif'
    matplotlib.rcParams['font.serif'] = [
        *( [name for name in preferred_serif_fonts if name in available_fonts] ),
        *[name for name in preferred_serif_fonts if name not in available_fonts],
        'DejaVu Serif',
    ]
    matplotlib.rcParams['font.sans-serif'] = [
        *( [name for name in preferred_sans_fonts if name in available_fonts] ),
        *[name for name in preferred_sans_fonts if name not in available_fonts],
        'DejaVu Sans',
        'Arial',
        'Liberation Sans',
    ]
    matplotlib.rcParams['axes.linewidth'] = 0.8
    matplotlib.rcParams['xtick.direction'] = 'in'
    matplotlib.rcParams['ytick.direction'] = 'in'
    matplotlib.rcParams['xtick.major.width'] = 0.8
    matplotlib.rcParams['ytick.major.width'] = 0.8
    matplotlib.rcParams['xtick.top'] = True
    matplotlib.rcParams['ytick.right'] = True
    matplotlib.rcParams['figure.dpi'] = 300
    matplotlib.rcParams['mathtext.fontset'] = 'stix'

    if not fallback_path:
        return

    original_findfont = font_manager.findfont

    def patched_findfont(prop, *args, **kwargs):
        try:
            if isinstance(prop, FontProperties):
                families = list(prop.get_family() or [])
            elif isinstance(prop, str):
                families = [prop]
            else:
                families = list(getattr(prop, 'get_family', lambda: [])() or [])

            if any(name in [*preferred_serif_fonts, *preferred_sans_fonts] for name in families):
                return fallback_path
        except Exception:
            pass
        return original_findfont(prop, *args, **kwargs)

    font_manager.findfont = patched_findfont

    # Silence repeated glyph warnings once a usable fallback is injected.
    warnings.filterwarnings('ignore', message=r'.*Glyph .* missing from font\(s\).*')


_patch_matplotlib()
