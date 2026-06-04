"""FloodMind TUI — Logo widget with shimmer animation."""

import math
from textual.widget import Widget
from rich.text import Text
from rich.console import RenderableType

from floodmind.tui.theme import C

LOGO_LINES = [
    " ███████ ██       ██████   ██████  ███    ███ ██ ███    ██ ██████ ",
    " ██      ██      ██    ██ ██    ██ ████  ████ ██ ████   ██ ██   ██",
    " █████   ██      ██    ██ ██    ██ ██ ████ ██ ██ ██ ██  ██ ██   ██",
    " ██      ██      ██    ██ ██    ██ ██  ██  ██ ██ ██  ██ ██ ██   ██",
    " ██      ███████  ██████   ██████  ██      ██ ██ ██   ████ ██████ ",
]


def _hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(r, g, b):
    return f"#{int(r):02x}{int(g):02x}{int(b):02x}"


def _lerp_color(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    r = max(0, min(255, int(r1 + (r2 - r1) * t)))
    g = max(0, min(255, int(g1 + (g2 - g1) * t)))
    b = max(0, min(255, int(b1 + (b2 - b1) * t)))
    return _rgb_to_hex(r, g, b)


def _render_line(line: str, phase: float, max_width: int) -> Text:
    muted = C["text_muted"]
    bright = C["text"]
    primary = C["primary"]
    peak = "#ffffff"
    half_width = max_width * 0.22
    text = Text()
    for i, ch in enumerate(line):
        if ch == " ":
            text.append(ch, style=muted)
            continue
        dist = abs(i - phase * max_width)
        intensity = math.exp(-((dist / max(half_width, 1)) ** 2))
        if intensity < 0.02:
            color = _lerp_color(muted, bright, 0.05)
        elif intensity < 0.4:
            color = _lerp_color(muted, bright, intensity * 1.8)
        elif intensity < 0.8:
            color = _lerp_color(bright, primary, (intensity - 0.4) * 2.5)
        else:
            color = _lerp_color(primary, peak, (intensity - 0.8) * 5.0)
        text.append(ch, style=color)
    return text


class LogoWidget(Widget):
    can_focus = False
    
    DEFAULT_CSS = """
    LogoWidget {
        width: 100%;
        height: auto;
        content-align: center middle;
        text-align: center;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._phase = 0.0
        self._direction = 1

    def on_mount(self) -> None:
        self.set_interval(0.04, self._advance)

    def _advance(self) -> None:
        self._phase += 0.015 * self._direction
        if self._phase > 1.15:
            self._direction = -1
        elif self._phase < -0.15:
            self._direction = 1
        self.refresh()

    def render(self) -> RenderableType:
        max_w = max(len(line) for line in LOGO_LINES)
        lines = [_render_line(line, self._phase, max_w) for line in LOGO_LINES]
        result = Text()
        for i, line in enumerate(lines):
            if i > 0:
                result.append("\n")
            result.append_text(line)
        return result
