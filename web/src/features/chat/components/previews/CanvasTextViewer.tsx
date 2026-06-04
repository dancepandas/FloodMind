import { useEffect, useRef, useCallback } from "react";

interface CanvasTextViewerProps {
  content: string;
}

const LINE_HEIGHT = 21;
const GUTTER_WIDTH = 56;
const PADDING_X = 16;
const PADDING_Y = 12;
const CHAR_WIDTH = 8.4;

const SYNTAX_COLORS: Record<string, string> = {
  keyword: "#c678dd",
  string: "#98c379",
  comment: "#5c6370",
  number: "#d19a66",
  func: "#61afef",
  default: "#abb2bf",
};

function tokenize(line: string): { text: string; color: string }[] {
  const commentMatch = line.match(/^(\s*#.*)/);
  if (commentMatch) return [{ text: line, color: SYNTAX_COLORS.comment }];

  const parts: { text: string; color: string }[] = [];
  const re = /("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|#[^\n]*|\b\d+\.?\d*\b|\b(def|class|return|if|else|elif|for|while|import|from|as|try|except|finally|with|yield|raise|pass|break|continue|and|or|not|in|is|None|True|False|lambda|async|await)\b)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(line)) !== null) {
    if (match.index > last) {
      parts.push({ text: line.slice(last, match.index), color: SYNTAX_COLORS.default });
    }
    const tok = match[1];
    if (tok.startsWith('"') || tok.startsWith("'")) parts.push({ text: tok, color: SYNTAX_COLORS.string });
    else if (tok.startsWith("#")) parts.push({ text: tok, color: SYNTAX_COLORS.comment });
    else if (/^\d/.test(tok)) parts.push({ text: tok, color: SYNTAX_COLORS.number });
    else parts.push({ text: tok, color: SYNTAX_COLORS.keyword });
    last = match.index + tok.length;
  }
  if (last < line.length) parts.push({ text: line.slice(last), color: SYNTAX_COLORS.default });
  return parts.length > 0 ? parts : [{ text: line, color: SYNTAX_COLORS.default }];
}

export function CanvasTextViewer({ content }: CanvasTextViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const scrollTopRef = useRef(0);
  const scrollLeftRef = useRef(0);
  const lines = content.split("\n");
  const maxLineChars = Math.max(...lines.map((l) => l.length), 1);
  const totalWidth = GUTTER_WIDTH + maxLineChars * CHAR_WIDTH + PADDING_X * 2;
  const totalHeight = lines.length * LINE_HEIGHT + PADDING_Y * 2;

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const dpr = window.devicePixelRatio || 1;
    const w = container.clientWidth;
    const h = container.clientHeight;
    if (w <= 0 || h <= 0) return;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = w + "px";
    canvas.style.height = h + "px";

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);

    const scrollTop = scrollTopRef.current;
    const scrollLeft = scrollLeftRef.current;

    // Background — warm dark
    const bgGrad = ctx.createLinearGradient(0, 0, 0, h);
    bgGrad.addColorStop(0, "#1a1d23");
    bgGrad.addColorStop(1, "#16181d");
    ctx.fillStyle = bgGrad;
    ctx.fillRect(0, 0, w, h);

    // Subtle grid texture
    ctx.fillStyle = "rgba(255,255,255,0.008)";
    for (let gy = 0; gy < h; gy += LINE_HEIGHT * 2) {
      ctx.fillRect(0, gy, w, 1);
    }

    // Gutter
    const gutterGrad = ctx.createLinearGradient(GUTTER_WIDTH, 0, 0, 0);
    gutterGrad.addColorStop(0, "rgba(0,0,0,0)");
    gutterGrad.addColorStop(1, "rgba(255,255,255,0.02)");
    ctx.fillStyle = gutterGrad;
    ctx.fillRect(0, 0, GUTTER_WIDTH, h);

    // Gutter right border
    ctx.fillStyle = "rgba(255,255,255,0.04)";
    ctx.fillRect(GUTTER_WIDTH - 1, 0, 1, h);

    // Visible range
    const startLine = Math.max(0, Math.floor(scrollTop / LINE_HEIGHT));
    const endLine = Math.min(lines.length, startLine + Math.ceil(h / LINE_HEIGHT) + 1);
    const yOffset = -(scrollTop % LINE_HEIGHT);

    ctx.font = '13px "JetBrains Mono", "Fira Code", "Cascadia Code", monospace';
    ctx.textBaseline = "middle";

    // Active line highlight
    for (let i = startLine; i < endLine; i++) {
      const y = PADDING_Y + (i - startLine) * LINE_HEIGHT + yOffset;
      const lineText = lines[i].trim();
      // Soft highlight on non-empty lines
      if (lineText.length > 0 && lineText[0] !== "#") {
        ctx.fillStyle = "rgba(255,255,255,0.012)";
        ctx.fillRect(0, y - LINE_HEIGHT / 2, w, LINE_HEIGHT);
      }
    }

    for (let i = startLine; i < endLine; i++) {
      const y = PADDING_Y + (i - startLine) * LINE_HEIGHT + yOffset;
      if (y + LINE_HEIGHT < 0 || y - LINE_HEIGHT > h) continue;

      // Line number
      ctx.fillStyle = (i + 1) % 5 === 0 ? "rgba(255,255,255,0.18)" : "rgba(255,255,255,0.09)";
      ctx.textAlign = "right";
      ctx.font = '11px "JetBrains Mono", monospace';
      ctx.fillText(String(i + 1), GUTTER_WIDTH - 12, y);

      // Text with syntax coloring
      const line = lines[i].replace(/\t/g, "    ");
      const isCode = line.match(/^[\s]*(\/\/|#|def |class |import |from |return |if |for |while |print|const |let |var |function)/);
      const tokens = isCode ? tokenize(line) : [{ text: line, color: "#8899aa" }];

      ctx.font = '13px "JetBrains Mono", monospace';
      let tx = GUTTER_WIDTH + PADDING_X - scrollLeft;
      for (const tok of tokens) {
        if (tx > w + 50 || tx < -200) { tx += ctx.measureText(tok.text).width; continue; }
        ctx.fillStyle = tok.color;
        ctx.textAlign = "left";
        ctx.fillText(tok.text, tx, y);
        tx += ctx.measureText(tok.text).width;
      }
    }
  }, [lines, totalWidth, totalHeight]);

  useEffect(() => { draw(); }, [draw]);

  const handleScroll = useCallback(() => {
    const c = containerRef.current;
    if (!c) return;
    scrollTopRef.current = c.scrollTop;
    scrollLeftRef.current = c.scrollLeft;
    draw();
  }, [draw]);

  return (
    <div className="flex flex-col h-full" style={{ background: "#1a1d23" }}>
      {/* Status bar */}
      <div
        className="flex items-center justify-between px-4 py-1.5 shrink-0 select-none"
        style={{
          background: "rgba(0,0,0,0.3)",
          borderBottom: "1px solid rgba(255,255,255,0.04)",
        }}
      >
        <div className="flex items-center gap-3 text-[11px]">
          <span style={{ color: "rgba(255,255,255,0.25)" }}>Plain Text</span>
          <span style={{ color: "rgba(255,255,255,0.15)" }}>UTF-8</span>
          <span style={{ color: "rgba(255,255,255,0.15)" }}>{lines.length} lines</span>
        </div>
        <div className="flex items-center gap-1.5 text-[10px]" style={{ color: "rgba(255,255,255,0.12)" }}>
          <span className="w-2 h-2 rounded-full" style={{ background: "#98c379" }} />
          LF
        </div>
      </div>

      {/* Canvas area */}
      <div
        ref={containerRef}
        className="flex-1 overflow-auto"
        onScroll={handleScroll}
      >
        <div style={{ width: totalWidth, height: totalHeight, position: "relative" }}>
          <canvas ref={canvasRef} className="absolute inset-0" />
        </div>
      </div>
    </div>
  );
}
