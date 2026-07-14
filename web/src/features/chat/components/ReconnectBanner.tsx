export function ReconnectBanner() {
  return (
    <div
      className="flex items-center gap-3 px-4 py-3 rounded-xl animate-scale-in"
      style={{
        background: "var(--banner-reconnect-bg)",
        border: "1px solid var(--banner-reconnect-border)",
        boxShadow: "0 4px 20px rgba(59,130,246,0.12)",
      }}
    >
      <div
        className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
        style={{ background: "var(--banner-reconnect-icon-bg)", color: "var(--banner-reconnect-text)" }}
      >
        <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
          <path d="M5 12.55a11 11 0 0 1 14.08 0" />
          <path d="M1.42 9a16 16 0 0 1 21.16 0" />
          <path d="M8.53 16.11a6 6 0 0 1 6.95 0" />
          <line x1="12" y1="20" x2="12.01" y2="20" />
        </svg>
      </div>
      <div className="flex-1 min-w-0">
        <div
          className="text-[12px] font-bold"
          style={{ color: "var(--banner-reconnect-text)" }}
        >
          连接已断开，正在自动重连…
        </div>
        <div
          className="text-[11px]"
          style={{ color: "var(--banner-reconnect-text)", opacity: 0.85 }}
        >
          请不要刷新页面，系统会尽快恢复对话
        </div>
      </div>
      <div className="flex-shrink-0">
        <div
          className="w-5 h-5 border-2 rounded-full animate-spin"
          style={{
            borderColor: "var(--banner-reconnect-text)",
            borderTopColor: "transparent",
          }}
        />
      </div>
    </div>
  );
}
