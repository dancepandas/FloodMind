import type { ActionDetail } from "@/types/app";

/* ─── Step Badge with tooltip ─── */
export function StepBadge({ index, type, label }: { index: number; type: "thought" | "action" | "answer"; label?: string }) {
  const palette = {
    thought: { bg: "var(--wave)", ring: "var(--accent-light)" },
    action: { bg: "var(--reef)", ring: "rgba(20,184,166,0.15)" },
    answer: { bg: "var(--wave)", ring: "var(--accent-light)" },
  }[type];
  const tooltipText = label || { thought: "思考推理", action: "工具调用", answer: "应答" }[type];
  return (
    <span className="codex-step-tooltip">
      <span
        className="inline-flex items-center justify-center w-[18px] h-[18px] rounded-md text-[9px] font-bold flex-shrink-0"
        style={{ background: palette.bg, color: "#fff", boxShadow: `0 0 0 2px ${palette.ring}` }}
      >
        {index}
      </span>
      <span className="tooltip-content">{tooltipText}</span>
    </span>
  );
}

/* ─── Step Complete Checkbox ─── */
export function StepComplete({ type }: { type: "thought" | "action" | "answer" }) {
  const borderColor = { thought: "var(--wave)", action: "var(--reef)", answer: "var(--wave)" }[type];
  return (
    <span className={`codex-step-check completed`} style={{ borderColor }}>
      <span className="checkmark" />
    </span>
  );
}

/* ─── Streaming: Pulse Dots Loader ─── */
export function StreamingIndicator({ variant = "ocean" }: { variant?: "ocean" | "teal" }) {
  return (
    <span className={`codex-pulse-dots ${variant === "teal" ? "teal" : ""}`}>
      <span className="dot" />
      <span className="dot" />
      <span className="dot" />
    </span>
  );
}

/* ─── Status Icon ─── */
export function StatusIcon({ status, size = 12 }: { status: ActionDetail["status"]; size?: number }) {
  if (status === "running" || status === "pending_confirmation") {
    return <StreamingIndicator variant="teal" />;
  }
  if (status === "done") {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="var(--reef)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="20 6 9 17 4 12" />
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="var(--alert)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}
