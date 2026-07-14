import { AlertCircle } from "lucide-react";

interface ErrorMessageProps {
  title?: string;
  message: string;
  onRetry?: () => void;
}

export function ErrorMessage({ title = "出错了", message, onRetry }: ErrorMessageProps) {
  return (
    <div
      className="flex flex-col gap-2 px-4 py-3 rounded-xl animate-shake"
      style={{
        background: "var(--banner-error-bg)",
        border: "1px solid var(--banner-error-border)",
      }}
    >
      <div className="flex items-center gap-2.5">
        <div
          className="w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0"
          style={{ background: "rgba(244,63,94,0.12)", color: "var(--alert)" }}
        >
          <AlertCircle size={16} strokeWidth={2} />
        </div>
        <div className="flex-1 min-w-0">
          <div
            className="text-[12px] font-bold"
            style={{ color: "var(--banner-error-title)" }}
          >
            {title}
          </div>
          <div
            className="text-[11px] leading-relaxed"
            style={{ color: "var(--banner-error-text)" }}
          >
            {message}
          </div>
        </div>
      </div>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="self-start px-3.5 py-1.5 text-[11px] font-bold rounded-lg transition-all duration-200 active:scale-[0.96]"
          style={{ background: "var(--alert)", color: "white" }}
        >
          重试
        </button>
      )}
    </div>
  );
}
