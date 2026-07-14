import { AlertCircle } from "lucide-react";

/** 错误块：红色左边框 + 警告图标。 */
export function ErrorBlock({ content }: { content: string }) {
  return (
    <div
      className="w-full px-3 py-2 rounded-lg text-[12px] leading-relaxed"
      style={{
        background: "var(--status-error-bg)",
        borderLeft: "2px solid var(--alert)",
        color: "var(--status-error-text)",
      }}
    >
      <div className="flex items-center gap-2">
        <AlertCircle size={13} strokeWidth={1.8} style={{ opacity: 0.7 }} />
        <span className="font-medium">{content}</span>
      </div>
    </div>
  );
}
