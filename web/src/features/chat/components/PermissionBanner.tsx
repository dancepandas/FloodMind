import { ShieldAlert, FileText } from "lucide-react";
import type { PendingPermissionAsk } from "@/types/app";

interface PermissionBannerProps {
  ask: PendingPermissionAsk;
  onRespond: (approved: boolean) => void;
}

export function PermissionBanner({ ask, onRespond }: PermissionBannerProps) {
  const isExitPlanMode = ask.toolName === "exit_plan_mode";
  const planSummary = isExitPlanMode
    ? String(ask.toolInput?.plan_summary ?? ask.askReason ?? "")
    : "";

  return (
    <div
      className="flex flex-col gap-3 px-4 py-3 rounded-xl animate-scale-in"
      style={{
        background: "var(--banner-permission-bg)",
        border: "1px solid var(--banner-permission-border)",
        boxShadow: "0 4px 20px rgba(245,158,11,0.15)",
        animation: "bannerPulse 2.5s ease-in-out infinite",
      }}
    >
      {/* 标题行 */}
      <div className="flex items-center gap-3">
        <div
          className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 animate-icon-wiggle"
          style={{ background: "var(--banner-permission-icon-bg)", color: "white" }}
        >
          <ShieldAlert size={18} strokeWidth={2} />
        </div>
        <div className="flex-1 min-w-0">
          <div
            className="text-[12px] font-bold"
            style={{ color: "var(--banner-permission-title)" }}
          >
            {isExitPlanMode ? "执行计划审批" : "权限确认请求"}
          </div>
          {!isExitPlanMode && ask.askReason && (
            <div
              className="text-[11px] leading-relaxed truncate"
              style={{ color: "var(--banner-permission-text)" }}
            >
              {ask.askReason}
            </div>
          )}
        </div>
        <div className="flex gap-2 flex-shrink-0">
          <button
            type="button"
            onClick={() => onRespond(false)}
            className="px-3.5 py-1.5 text-[11px] font-bold rounded-lg transition-all duration-200 active:scale-[0.96]"
            style={{ background: "var(--surface)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
          >
            拒绝
          </button>
          <button
            type="button"
            onClick={() => onRespond(true)}
            className="px-3.5 py-1.5 text-[11px] font-bold rounded-lg transition-all duration-200 active:scale-[0.96]"
            style={{ background: "var(--sand)", color: "white" }}
          >
            允许执行
          </button>
        </div>
      </div>

      {/* exit_plan_mode 专属：计划摘要卡 */}
      {isExitPlanMode && planSummary && (
        <div
          className="flex items-start gap-2.5 px-3 py-2.5 rounded-lg"
          style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
        >
          <FileText
            size={14}
            strokeWidth={1.8}
            className="flex-shrink-0 mt-0.5"
            style={{ color: "var(--text-tertiary)" }}
          />
          <div
            className="text-[11px] leading-relaxed whitespace-pre-wrap flex-1 min-w-0"
            style={{ color: "var(--text-secondary)" }}
          >
            {planSummary}
          </div>
        </div>
      )}
    </div>
  );
}
