import { useState } from "react";
import {
  FileText,
  Image as ImageIcon,
  Table,
  Download,
  Eye,
  Layers,
  ListTree,
  CheckCircle2,
  Circle,
  Loader2,
  XCircle,
  Minimize2,
} from "lucide-react";
import type {
  GeneratedArtifact,
  TokenUsage,
  WorkflowPlan,
  WorkflowStepItem,
} from "@/types/app";
import { resolveMediaUrl } from "@/api/client";
import { SectionHeader } from "@/features/context/components/SectionHeader";
import { createLogger } from "@/lib/logger";

const log = createLogger("RightPanel");

const MAX_HISTORY = 10;

function formatNumber(n: number): string {
  return n.toLocaleString("zh-CN");
}

function SparkleIcon({ size = 12, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className}>
      <path d="M12 0L13.5 8.5L22 6L15 12L22 18L13.5 15.5L12 24L10.5 15.5L2 18L9 12L2 6L10.5 8.5L12 0Z" fill="currentColor" />
    </svg>
  );
}

/* ═══════════════════════════════════════
   Runtime header
   ═══════════════════════════════════════ */
function RuntimeHeader({
  isStreaming,
  isPaused,
  isContextCompressing,
}: {
  isStreaming?: boolean;
  isPaused?: boolean;
  isContextCompressing?: boolean;
}) {
  let statusNode: React.ReactNode;
  if (isContextCompressing) {
    statusNode = (
      <div className="flex items-center gap-1.5">
        <Minimize2 size={10} className="animate-pulse" style={{ color: "var(--sand)" }} />
        <span className="text-[9px] font-semibold" style={{ color: "var(--sand)" }}>压缩上下文</span>
      </div>
    );
  } else if (isStreaming) {
    statusNode = (
      <div className="flex items-center gap-1.5">
        <div className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: "var(--wave)" }} />
        <span className="text-[9px] font-semibold" style={{ color: "var(--wave)" }}>运行中</span>
      </div>
    );
  } else if (isPaused) {
    statusNode = (
      <div className="flex items-center gap-1.5">
        <div className="w-1.5 h-1.5 rounded-full" style={{ background: "var(--sand)" }} />
        <span className="text-[9px] font-semibold" style={{ color: "var(--sand)" }}>已暂停</span>
      </div>
    );
  } else {
    statusNode = (
      <div className="flex items-center gap-1.5">
        <div className="w-1.5 h-1.5 rounded-full animate-pulse-subtle" style={{ background: "var(--reef)" }} />
        <span className="text-[9px] font-semibold" style={{ color: "var(--text-tertiary)" }}>就绪</span>
      </div>
    );
  }

  return (
    <div
      className="shrink-0 px-4 py-3.5 flex items-center gap-2.5"
      style={{ borderBottom: "1px solid var(--border)" }}
    >
      <div
        className="w-8 h-8 rounded-xl flex items-center justify-center"
        style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" style={{ color: "var(--wave)" }}>
          <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
      <div>
        <span
          className="text-[16px]"
          style={{ fontFamily: "var(--font-display)", color: "var(--text-primary)" }}
        >
          会话概览
        </span>
      </div>
      <div className="ml-auto">
        {statusNode}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════
   Token usage
   ═══════════════════════════════════════ */
function TokenUsageSection({ usage, history }: { usage: TokenUsage; history: TokenUsage[] }) {
  const [expanded, setExpanded] = useState(true);
  const total = Math.max(usage.total_tokens, 1);
  const inputPct = (usage.prompt_tokens / total) * 100;
  const outputPct = (usage.completion_tokens / total) * 100;
  const chartData = history.slice(-MAX_HISTORY);

  return (
    <div style={{ borderBottom: "1px solid var(--border)" }}>
      <SectionHeader
        title="Token 用量"
        icon={Layers}
        expanded={expanded}
        onToggle={() => setExpanded((v) => !v)}
      />
      {expanded && (
        <div className="px-4 pb-4 space-y-2">
          <div className="flex justify-between text-[11px]">
            <span style={{ color: "var(--text-secondary)" }}>输入（prompt）</span>
            <span className="font-mono" style={{ color: "var(--text-primary)" }}>{formatNumber(usage.prompt_tokens)}</span>
          </div>
          <div className="h-1 rounded-full overflow-hidden" style={{ background: "var(--surface-3)" }}>
            <div className="h-full rounded-full" style={{ width: `${inputPct}%`, background: "var(--wave)" }} />
          </div>

          <div className="flex justify-between text-[11px]">
            <span style={{ color: "var(--text-secondary)" }}>输出（completion）</span>
            <span className="font-mono" style={{ color: "var(--text-primary)" }}>{formatNumber(usage.completion_tokens)}</span>
          </div>
          <div className="h-1 rounded-full overflow-hidden" style={{ background: "var(--surface-3)" }}>
            <div className="h-full rounded-full" style={{ width: `${outputPct}%`, background: "var(--reef)" }} />
          </div>

          <div
            className="flex justify-between text-[11px] pt-2 mt-1 font-bold"
            style={{ borderTop: "1px solid var(--border)", color: "var(--text-primary)" }}
          >
            <span>总计</span>
            <span className="font-mono">{formatNumber(usage.total_tokens)}</span>
          </div>

          <div className="flex items-center gap-4 text-[10px]">
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: "var(--wave)" }} />
              <span style={{ color: "var(--text-secondary)" }}>输入</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: "var(--reef)" }} />
              <span style={{ color: "var(--text-secondary)" }}>输出</span>
            </div>
          </div>

          {chartData.length > 0 && (
            <div className="flex items-end gap-[2px] h-7 mt-2">
              {chartData.map((u, i) => {
                const max = Math.max(...chartData.map((x) => x.total_tokens), 1);
                const inH = Math.max((u.prompt_tokens / max) * 100, 8);
                const outH = Math.max((u.completion_tokens / max) * 100, 8);
                return (
                  <div key={i} className="flex-1 flex items-end gap-[1px]">
                    <div
                      className="flex-1 rounded-t-sm transition-opacity hover:opacity-70"
                      style={{ height: `${inH}%`, background: "var(--wave)", opacity: 0.35 }}
                      title={`输入 ${u.prompt_tokens}`}
                    />
                    <div
                      className="flex-1 rounded-t-sm transition-opacity hover:opacity-70"
                      style={{ height: `${outH}%`, background: "var(--reef)", opacity: 0.35 }}
                      title={`输出 ${u.completion_tokens}`}
                    />
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════
   Workflow plan
   ═══════════════════════════════════════ */
function StepDot({ status }: { status: WorkflowStepItem["status"] }) {
  if (status === "completed") {
    return (
      <div
        className="w-[18px] h-[18px] rounded-full flex items-center justify-center flex-shrink-0"
        style={{ background: "var(--status-completed-bg)", color: "var(--status-completed-text)" }}
      >
        <CheckCircle2 size={10} strokeWidth={2.5} />
      </div>
    );
  }
  if (status === "running") {
    return (
      <div
        className="w-[18px] h-[18px] rounded-full flex items-center justify-center flex-shrink-0"
        style={{ background: "var(--status-running-bg)", color: "var(--status-running-text)" }}
      >
        <Loader2 size={10} className="animate-spin" strokeWidth={2.5} />
      </div>
    );
  }
  if (status === "error") {
    return (
      <div
        className="w-[18px] h-[18px] rounded-full flex items-center justify-center flex-shrink-0"
        style={{ background: "var(--status-error-bg)", color: "var(--status-error-text)" }}
      >
        <XCircle size={10} strokeWidth={2.5} />
      </div>
    );
  }
  return (
    <div
      className="w-[18px] h-[18px] rounded-full flex items-center justify-center flex-shrink-0"
      style={{ background: "var(--status-pending-bg)", color: "var(--status-pending-text)" }}
    >
      <Circle size={8} strokeWidth={2} />
    </div>
  );
}

function PlanSection({ workflow }: { workflow?: WorkflowPlan | null }) {
  const [expanded, setExpanded] = useState(true);
  const steps = workflow?.steps || [];
  const completedSteps = steps.filter((s) => s.status === "completed").length;
  const totalSteps = steps.length;

  return (
    <div className="flex-1 min-h-0 flex flex-col" style={{ borderBottom: "1px solid var(--border)" }}>
      <SectionHeader
        title="执行计划"
        icon={ListTree}
        expanded={expanded}
        onToggle={() => setExpanded((v) => !v)}
        badge={
          totalSteps > 0 ? (
            <div className="flex items-center gap-1.5">
              <div className="h-1 w-14 rounded-full overflow-hidden" style={{ background: "var(--surface-3)" }}>
                <div className="h-full rounded-full transition-all duration-500" style={{ width: `${(completedSteps / totalSteps) * 100}%`, background: "var(--reef)" }} />
              </div>
              <span className="text-[10px] font-bold font-mono" style={{ color: "var(--text-tertiary)" }}>
                {completedSteps}/{totalSteps}
              </span>
            </div>
          ) : undefined
        }
      />
      {expanded && (
        <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-3">
          {steps.length === 0 ? (
            <div className="py-6 flex flex-col items-center gap-2">
              <ListTree size={26} style={{ color: "var(--text-tertiary)", opacity: 0.25 }} strokeWidth={1.5} />
              <span className="text-[11px]" style={{ color: "var(--text-tertiary)", opacity: 0.7 }}>
                等待任务规划
              </span>
            </div>
          ) : (
            <div className="flex flex-col">
              {steps.map((step, index) => (
                <div
                  key={step.key || `${index}`}
                  className="flex items-start gap-2.5 py-2"
                  style={{ borderBottom: index < steps.length - 1 ? "1px solid var(--border)" : "none" }}
                >
                  <StepDot status={step.status} />
                  <div className="flex-1 min-w-0 pt-0.5">
                    <div
                      className="text-[12px] font-semibold leading-snug"
                      style={{
                        color:
                          step.status === "error"
                            ? "var(--alert)"
                            : step.status === "completed"
                            ? "var(--text-primary)"
                            : "var(--text-secondary)",
                      }}
                    >
                      {step.title || step.label}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════
   Artifacts list
   ═══════════════════════════════════════ */
function getArtifactType(filename: string): "image" | "pdf" | "csv" | "excel" | "doc" | "other" {
  const ext = filename.split(".").pop()?.toLowerCase() || "";
  if (["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"].includes(ext)) return "image";
  if (ext === "pdf") return "pdf";
  if (ext === "csv") return "csv";
  if (["xlsx", "xls"].includes(ext)) return "excel";
  if (["docx", "doc"].includes(ext)) return "doc";
  return "other";
}

function getArtifactListIcon(type: ReturnType<typeof getArtifactType>) {
  switch (type) {
    case "image":
      return { Icon: ImageIcon, color: "#ffffff", bg: "var(--wave)" };
    case "pdf":
      return { Icon: FileText, color: "#ffffff", bg: "var(--sand)" };
    case "csv":
    case "excel":
      return { Icon: Table, color: "#ffffff", bg: "var(--reef)" };
    case "doc":
      return { Icon: FileText, color: "#ffffff", bg: "#6366f1" };
    default:
      return { Icon: FileText, color: "#ffffff", bg: "var(--text-tertiary)" };
  }
}

function ArtifactsSection({ artifacts, sessionId }: { artifacts: GeneratedArtifact[]; sessionId: string }) {
  const [expanded, setExpanded] = useState(true);

  return (
    <div className="flex-1 min-h-0 flex flex-col" style={{ borderBottom: "1px solid var(--border)" }}>
      <SectionHeader
        title="产物"
        icon={FileText}
        expanded={expanded}
        onToggle={() => setExpanded((v) => !v)}
        badge={
          artifacts.length > 0 ? (
            <span
              className="text-[9px] font-bold px-1.5 py-0.5 rounded-full"
              style={{ background: "var(--surface-2)", color: "var(--text-secondary)" }}
            >
              {artifacts.length}
            </span>
          ) : undefined
        }
      />
      {expanded && (
        <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-3">
          {artifacts.length === 0 ? (
            <div className="py-6 flex flex-col items-center gap-2">
              <FileText size={26} style={{ color: "var(--text-tertiary)", opacity: 0.25 }} strokeWidth={1.5} />
              <span className="text-[11px]" style={{ color: "var(--text-tertiary)", opacity: 0.7 }}>
                暂无产物
              </span>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {artifacts.map((artifact) => {
                const type = getArtifactType(artifact.filename);
                const { Icon, bg } = getArtifactListIcon(type);
                const downloadUrl = resolveMediaUrl(
                  artifact.download_url || `/api/sessions/${sessionId}/outputs/${encodeURIComponent(artifact.filename)}`
                );
                const previewUrl = resolveMediaUrl(artifact.image_url || downloadUrl);
                return (
                  <div
                    key={artifact.filename}
                    className="group flex items-center gap-3 p-2.5 rounded-xl transition-all duration-200"
                    style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.borderColor = "var(--border-strong)";
                      e.currentTarget.style.background = "var(--surface-2)";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.borderColor = "var(--border)";
                      e.currentTarget.style.background = "var(--surface)";
                    }}
                  >
                    <div
                      className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 text-white"
                      style={{ background: bg }}
                    >
                      <Icon size={14} strokeWidth={2} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-[11px] font-semibold truncate" style={{ color: "var(--text-primary)" }}>
                        {artifact.filename}
                      </div>
                      <div className="text-[9px]" style={{ color: "var(--text-secondary)" }}>
                        {artifact.size ? `${(artifact.size / 1024).toFixed(1)} KB` : "产物"}
                      </div>
                    </div>
                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      <a
                        href={previewUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="w-7 h-7 rounded-md flex items-center justify-center transition-colors"
                        style={{ background: "var(--surface-2)", color: "var(--text-secondary)" }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.background = "var(--wave)";
                          e.currentTarget.style.color = "white";
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.background = "var(--surface-2)";
                          e.currentTarget.style.color = "var(--text-secondary)";
                        }}
                        title="预览"
                      >
                        <Eye size={13} strokeWidth={2} />
                      </a>
                      <a
                        href={downloadUrl}
                        download={artifact.filename}
                        className="w-7 h-7 rounded-md flex items-center justify-center transition-colors"
                        style={{ background: "var(--surface-2)", color: "var(--text-secondary)" }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.background = "var(--wave)";
                          e.currentTarget.style.color = "white";
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.background = "var(--surface-2)";
                          e.currentTarget.style.color = "var(--text-secondary)";
                        }}
                        title="下载"
                      >
                        <Download size={13} strokeWidth={2} />
                      </a>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════
   RightPanel
   ═══════════════════════════════════════ */
interface RightPanelProps {
  sessionId: string;
  tokenUsage: TokenUsage;
  tokenHistory: TokenUsage[];
  workflow?: WorkflowPlan | null;
  artifacts: GeneratedArtifact[];
  isStreaming?: boolean;
  isPaused?: boolean;
  isContextCompressing?: boolean;
}

export function RightPanel({
  sessionId,
  tokenUsage,
  tokenHistory,
  workflow,
  artifacts,
  isStreaming,
  isPaused,
  isContextCompressing,
}: RightPanelProps) {
  return (
    <div
      className="w-[340px] h-full flex flex-col flex-shrink-0 overflow-hidden animate-slide-in-right"
      style={{
        background: "var(--surface)",
        borderLeft: "1px solid var(--border)",
      }}
    >
      <RuntimeHeader isStreaming={isStreaming} isPaused={isPaused} isContextCompressing={isContextCompressing} />
      <TokenUsageSection usage={tokenUsage} history={tokenHistory} />
      <PlanSection workflow={workflow} />
      <ArtifactsSection artifacts={artifacts} sessionId={sessionId} />
    </div>
  );
}
