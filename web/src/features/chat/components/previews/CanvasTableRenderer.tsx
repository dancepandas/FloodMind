import { useEffect, useRef, useCallback, useState } from "react";

interface CanvasTableRendererProps {
  columns: string[];
  rows: string[][];
}

const ROW_HEIGHT = 32;
const HEADER_HEIGHT = 40;
const MIN_COL_WIDTH = 100;
const MAX_COL_WIDTH = 300;
const PADDING_CELL = 14;
const FROZEN_COLS = 1;

export function CanvasTableRenderer({ columns, rows }: CanvasTableRendererProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [colWidths, setColWidths] = useState<number[]>([]);

  useEffect(() => {
    const widths = columns.map((col, i) => {
      let maxLen = col.length;
      const sampleSize = Math.min(rows.length, 300);
      for (let r = 0; r < sampleSize; r++) {
        maxLen = Math.max(maxLen, String(rows[r]?.[i] ?? "").length);
      }
      // Approximate: CJK chars ~14px, Latin ~8px
      const cjkCount = (col.match(/[一-鿿]/g) || []).length;
      const latinCount = maxLen - cjkCount;
      const estWidth = cjkCount * 14 + latinCount * 8.5;
      return Math.max(MIN_COL_WIDTH, Math.min(MAX_COL_WIDTH, Math.ceil(estWidth) + PADDING_CELL * 2));
    });
    setColWidths(widths);
  }, [columns, rows]);

  const totalWidth = colWidths.reduce((a, b) => a + b, 0);
  const totalHeight = HEADER_HEIGHT + rows.length * ROW_HEIGHT;

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container || colWidths.length === 0) return;

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

    const scrollTop = container.scrollTop;
    const scrollLeft = container.scrollLeft;

    // Background
    ctx.fillStyle = "#1a1d23";
    ctx.fillRect(0, 0, w, h);

    // Find visible column range
    let colStart = 0;
    let colOffsetX = 0;
    let acc = 0;
    for (let i = 0; i < colWidths.length; i++) {
      if (acc + colWidths[i] > scrollLeft) { colStart = i; colOffsetX = acc - scrollLeft; break; }
      acc += colWidths[i];
    }
    const visibleCols: number[] = [];
    let dxAcc = colOffsetX;
    for (let i = colStart; i < colWidths.length && dxAcc < w + 50; i++) {
      visibleCols.push(i);
      dxAcc += colWidths[i];
    }

    if (visibleCols.length === 0) return;

    // --- Header ---
    const headerGrad = ctx.createLinearGradient(0, 0, 0, HEADER_HEIGHT);
    headerGrad.addColorStop(0, "rgba(37, 99, 168, 0.25)");
    headerGrad.addColorStop(0.5, "rgba(37, 99, 168, 0.15)");
    headerGrad.addColorStop(1, "rgba(16, 185, 129, 0.08)");
    ctx.fillStyle = headerGrad;
    ctx.fillRect(0, 0, w, HEADER_HEIGHT);

    // Header bottom glow line
    const glowGrad = ctx.createLinearGradient(0, HEADER_HEIGHT - 2, 0, HEADER_HEIGHT);
    glowGrad.addColorStop(0, "rgba(37, 99, 168, 0.4)");
    glowGrad.addColorStop(1, "rgba(16, 185, 129, 0.15)");
    ctx.fillStyle = glowGrad;
    ctx.fillRect(0, HEADER_HEIGHT - 2, w, 2);

    ctx.font = '600 12px "Inter", system-ui, sans-serif';
    ctx.textBaseline = "middle";

    let hdx = colOffsetX;
    for (const ci of visibleCols) {
      const cw = colWidths[ci];
      ctx.fillStyle = "#e2e8f0";
      ctx.textAlign = "left";
      const text = columns[ci] || "";
      const metrics = ctx.measureText(text);
      const tx = metrics.width > cw - PADDING_CELL * 2
        ? text.slice(0, Math.floor((cw - PADDING_CELL * 2 - 20) / (metrics.width / text.length))) + "…"
        : text;
      ctx.fillText(tx, hdx + PADDING_CELL, HEADER_HEIGHT / 2);
      hdx += cw;
    }

    // --- Rows ---
    const startRow = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT));
    const endRow = Math.min(rows.length, startRow + Math.ceil(h / ROW_HEIGHT) + 1);

    ctx.font = '12px "Inter", system-ui, sans-serif';

    for (let r = startRow; r < endRow; r++) {
      const y = HEADER_HEIGHT + r * ROW_HEIGHT;
      if (y + ROW_HEIGHT < HEADER_HEIGHT || y > h + ROW_HEIGHT) continue;

      const displayY = y - scrollTop + HEADER_HEIGHT;

      // Row background
      if (r % 2 === 0) {
        ctx.fillStyle = "rgba(255,255,255,0.015)";
      } else {
        ctx.fillStyle = "rgba(255,255,255,0.005)";
      }
      ctx.fillRect(0, displayY, w, ROW_HEIGHT);

      // Bottom border
      ctx.fillStyle = "rgba(255,255,255,0.025)";
      ctx.fillRect(0, displayY + ROW_HEIGHT - 1, w, 1);

      // Row number (frozen)
      if (colStart > 0) {
        ctx.fillStyle = "rgba(255,255,255,0.1)";
        ctx.textAlign = "right";
        ctx.fillText(String(r + 1), colOffsetX - 6, displayY + ROW_HEIGHT / 2);
      }

      // Cells
      hdx = colOffsetX;
      const row = rows[r] || [];
      for (const ci of visibleCols) {
        const cw = colWidths[ci];
        const val = row[ci] ?? "";

        ctx.fillStyle = ci === 0 ? "rgba(255,255,255,0.75)" : "rgba(255,255,255,0.5)";
        ctx.textAlign = "left";

        const text = String(val);
        const metrics = ctx.measureText(text);
        const display = metrics.width > cw - PADDING_CELL * 2
          ? text.slice(0, Math.floor((cw - PADDING_CELL * 2 - 16) / (metrics.width / text.length))) + "…"
          : text;
        ctx.fillText(display, hdx + PADDING_CELL, displayY + ROW_HEIGHT / 2);
        hdx += cw;
      }
    }

    // Bottom gradient fade
    const fadeGrad = ctx.createLinearGradient(0, h - 20, 0, h);
    fadeGrad.addColorStop(0, "rgba(26,29,35,0)");
    fadeGrad.addColorStop(1, "rgba(26,29,35,1)");
    ctx.fillStyle = fadeGrad;
    ctx.fillRect(0, h - 20, w, 20);
  }, [columns, rows, colWidths]);

  useEffect(() => { if (colWidths.length > 0) draw(); }, [colWidths, draw]);

  const handleScroll = useCallback(() => { draw(); }, [draw]);

  if (colWidths.length === 0) {
    return (
      <div className="flex items-center justify-center h-full" style={{ background: "#1a1d23" }}>
        <div className="flex flex-col items-center gap-2" style={{ color: "rgba(255,255,255,0.2)" }}>
          <div className="w-5 h-5 border-2 rounded-full animate-spin" style={{ borderColor: "rgba(37,99,168,0.3)", borderTopColor: "rgba(37,99,168,0.6)" }} />
          <span className="text-xs">计算列宽…</span>
        </div>
      </div>
    );
  }

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
          <span style={{ color: "rgba(255,255,255,0.25)" }}>Table</span>
          <span style={{ color: "rgba(255,255,255,0.15)" }}>
            {rows.length} rows × {columns.length} cols
          </span>
        </div>
        <div className="flex items-center gap-1 text-[10px]" style={{ color: "rgba(255,255,255,0.12)" }}>
          <span>{FROZEN_COLS} col frozen</span>
        </div>
      </div>

      {/* Table area */}
      <div ref={containerRef} className="flex-1 overflow-auto" onScroll={handleScroll}>
        <div style={{ width: totalWidth, height: totalHeight, position: "relative" }}>
          <canvas ref={canvasRef} className="absolute inset-0 pointer-events-none" />
        </div>
      </div>
    </div>
  );
}
