import { useRef, useState, useEffect } from "react";
import { Send, Pause, Paperclip, ChevronDown, ShieldAlert, Brain, Globe, Database } from "lucide-react";
import { useIsMobile } from "@/hooks/use-mobile";
import type { ModelOption, SessionConfig, PendingPermissionAsk } from "@/types/app";

const PINNED_MODELS = ["deepseek_v4_flash", "glm_51", "qwen_36_plus", "minimax_m25"];

function sortModels(models: ModelOption[]): ModelOption[] {
  const pinned: ModelOption[] = [];
  const rest: ModelOption[] = [];
  for (const m of models) {
    const idx = PINNED_MODELS.indexOf(m.key);
    if (idx >= 0) {
      pinned[idx] = m;
    } else {
      rest.push(m);
    }
  }
  return [...pinned.filter(Boolean), ...rest];
}

const MODEL_ICON_MAP: Record<string, string> = {
  qwen_35_plus: "qwen",
  qwen_36_plus: "qwen",
  glm_51: "zhipu",
  glm_5: "zhipu",
  deepseek_v4_pro: "deepseek",
  deepseek_v4_flash: "deepseek",
  kimi_k2_5: "kimi",
  kimi_k2_6: "kimi",
  minimax_m25: "minimax",
  minimax_m21: "minimax",
};

const SVG_ICONS_WITH_WHITE_FILL = new Set(["kimi"]);

function getModelIconUrl(modelKey: string): string | null {
  const id = MODEL_ICON_MAP[modelKey];
  if (!id) return null;
  if (SVG_ICONS_WITH_WHITE_FILL.has(id)) {
    return `https://registry.npmmirror.com/@lobehub/icons-static-png/latest/files/light/${id}-color.png`;
  }
  return `https://registry.npmmirror.com/@lobehub/icons-static-svg/latest/files/icons/${id}-color.svg`;
}

function ModelIcon({ modelKey, size = 14 }: { modelKey: string; size?: number }) {
  const url = getModelIconUrl(modelKey);
  if (!url) return null;
  return <img src={url} alt="" width={size} height={size} className="flex-shrink-0" style={{ objectFit: "contain" }} />;
}

interface ChatComposerProps {
  value: string;
  disabled?: boolean;
  isRunning?: boolean;
  isReconnecting?: boolean;
  models: ModelOption[];
  config: SessionConfig;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onPause: () => void;
  onUpload: (file: File) => void;
  onConfigChange: (config: SessionConfig) => void;
  pendingPermissionAsk: PendingPermissionAsk | null;
  onRespondPermissionAsk: (approved: boolean) => void;
}

export function ChatComposer({
  value,
  disabled,
  isRunning,
  isReconnecting,
  models,
  config,
  onChange,
  onSubmit,
  onPause,
  onUpload,
  onConfigChange,
  pendingPermissionAsk,
  onRespondPermissionAsk,
}: ChatComposerProps) {
  const isMobile = useIsMobile();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);

  const currentModel = models.find((m) => m.key === config.model_key);
  const sortedModels = sortModels(models);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 220) + "px";
    }
  }, [value]);

  function selectModel(model: ModelOption) {
    setModelMenuOpen(false);
    const next: SessionConfig = { ...config, model_key: model.key };
    if (!model.supports_reasoning) next.enable_reasoning = false;
    onConfigChange(next);
  }

  function toggleReasoning() {
    if (!currentModel?.supports_reasoning) return;
    onConfigChange({ ...config, enable_reasoning: !config.enable_reasoning });
  }

  function toggleSearch() {
    onConfigChange({ ...config, enable_search: !config.enable_search });
  }

  function toggleRag() {
    onConfigChange({ ...config, enable_rag: !config.enable_rag });
  }

  const sendBtnStyle = isRunning
    ? { background: 'var(--amber-500)', color: 'white', boxShadow: '0 2px 10px rgba(245,158,11,0.3)' }
    : value.trim().length > 0 && !disabled
    ? { background: 'var(--gradient-ocean-teal)', color: 'white', boxShadow: '0 2px 12px rgba(37,99,168,0.25)' }
    : { background: 'hsl(var(--muted))', color: 'hsl(var(--muted-foreground))', opacity: 0.25 };

  return (
    <div className="px-4 pt-2.5 pb-3" style={{ background: 'linear-gradient(to top, hsl(var(--background)), hsl(var(--background)))' }}>
      <div className={`${isMobile ? 'w-full' : 'max-w-[780px]'} mx-auto`}>
        {pendingPermissionAsk && (
          <div
            className="mb-2.5 flex items-center gap-2.5 px-4 py-2.5 rounded-xl animate-scale-in"
            style={{
              background: 'var(--amber-50)',
              border: '1px solid var(--amber-200)',
              boxShadow: '0 2px 8px rgba(245,158,11,0.08)',
            }}
          >
            <ShieldAlert size={15} style={{ color: 'var(--amber-500)' }} strokeWidth={2} />
            <div className="flex-1 min-w-0">
              <div className="text-[12px] font-semibold" style={{ color: 'var(--amber-800)' }}>权限确认</div>
              {pendingPermissionAsk.askReason && (
                <div className="text-[11px] truncate" style={{ color: 'var(--amber-700)', opacity: 0.6 }}>{pendingPermissionAsk.askReason}</div>
              )}
            </div>
            <div className="flex gap-2 flex-shrink-0">
              <button
                type="button"
                className="px-3.5 py-1.5 text-[11px] font-semibold rounded-lg transition-all duration-200 active:scale-[0.96]"
                style={{ background: 'var(--teal-500)', color: 'white', boxShadow: '0 1px 6px rgba(16,185,129,0.2)' }}
                onClick={() => onRespondPermissionAsk(true)}
              >
                允许
              </button>
              <button
                type="button"
                className="px-3.5 py-1.5 text-[11px] font-semibold rounded-lg transition-all duration-200 active:scale-[0.96]"
                style={{ background: 'hsl(var(--destructive))', color: 'white' }}
                onClick={() => onRespondPermissionAsk(false)}
              >
                拒绝
              </button>
            </div>
          </div>
        )}

        {isReconnecting && (
          <div
            className="mb-2.5 flex items-center gap-2.5 px-4 py-2.5 rounded-xl animate-scale-in"
            style={{
              background: 'var(--ocean-50)',
              border: '1px solid var(--ocean-200)',
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" style={{ color: 'var(--ocean-500)' }} className="animate-spin">
              <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" opacity="0.3" fill="none" />
              <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" fill="none" />
            </svg>
            <span className="text-[12px] font-medium" style={{ color: 'var(--ocean-500)' }}>
              连接已断开，正在自动重连...
            </span>
          </div>
        )}

        <div
          className="relative flex items-end rounded-2xl transition-all duration-300 animate-border-glow"
          style={{
            background: 'var(--gradient-card)',
            border: '1px solid hsl(var(--border))',
            boxShadow: '0 4px 24px -4px rgba(15,31,56,0.08)',
            backdropFilter: 'blur(8px)',
          }}
        >
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={isRunning}
            className="p-3 h-[52px] flex items-center justify-center flex-shrink-0 rounded-l-xl transition-all duration-200 disabled:opacity-25"
            style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.3 }}
            onMouseEnter={(e) => { if (!isRunning) { e.currentTarget.style.color = 'var(--ocean-500)'; e.currentTarget.style.opacity = '1'; e.currentTarget.style.background = 'var(--ocean-50)'; }}}
            onMouseLeave={(e) => { e.currentTarget.style.color = 'hsl(var(--muted-foreground))'; e.currentTarget.style.opacity = '0.3'; e.currentTarget.style.background = 'transparent'; }}
          >
            <Paperclip size={16} strokeWidth={1.8} />
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.xlsx,.xls,.txt,.json,.docx,.pdf,.md"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onUpload(file);
              event.currentTarget.value = "";
            }}
          />

          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              if (isRunning) return;
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onSubmit();
              }
            }}
            placeholder="输入预报任务指令..."
            className="flex-1 max-h-[220px] min-h-[52px] py-3.5 px-2 bg-transparent resize-none outline-none text-[14px] leading-relaxed placeholder:opacity-25"
            style={{ color: 'hsl(var(--foreground))', fontFamily: 'var(--font-body)' }}
            rows={1}
            disabled={disabled || isRunning}
          />

          <div className="flex items-center gap-0.5 p-1.5 h-[52px] flex-shrink-0 rounded-r-xl">
            <button
              type="button"
              onClick={isRunning ? onPause : onSubmit}
              disabled={!isRunning && (disabled || !value.trim())}
              className="p-2.5 rounded-xl flex items-center justify-center transition-all duration-300 active:scale-[0.92]"
              style={sendBtnStyle}
            >
              {isRunning ? <Pause size={14} /> : <Send size={14} className={value.trim().length > 0 ? "ml-0.5" : ""} />}
            </button>
          </div>
        </div>

        <div className="flex items-center gap-1.5 mt-2 px-0.5">
          <div className="relative">
            <button
              onClick={() => setModelMenuOpen(!modelMenuOpen)}
              className="flex items-center gap-1.5 px-2 py-0.5 rounded-md transition-all duration-200 text-[11px]"
              style={{
                background: modelMenuOpen ? 'hsl(var(--muted))' : 'hsl(var(--muted))',
                border: '1px solid hsl(var(--border))',
                opacity: modelMenuOpen ? 1 : 0.55,
              }}
            >
              <ModelIcon modelKey={config.model_key} size={13} />
              <span className="font-medium truncate max-w-[120px]" style={{ color: 'hsl(var(--foreground))', opacity: 0.8 }}>
                {currentModel?.label || config.model_key}
              </span>
              <ChevronUp size={10} className={`transition-transform duration-250 ${modelMenuOpen ? "rotate-180" : ""}`} style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.45 }} />
            </button>

            {modelMenuOpen && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setModelMenuOpen(false)} />
                <div
                  className="absolute bottom-full left-0 mb-1.5 z-50 min-w-[240px] py-0.5 max-h-[230px] overflow-y-auto rounded-xl"
                  style={{
                    background: 'rgba(255, 255, 255, 0.97)',
                    border: '1px solid hsl(var(--border))',
                    boxShadow: '0 12px 40px -8px rgba(15,31,56,0.18)',
                    backdropFilter: 'blur(8px)',
                  }}
                >
                  {sortedModels.map((model) => {
                    const isActive = model.key === config.model_key;
                    return (
                      <button
                        key={model.key}
                        onClick={() => selectModel(model)}
                        className="w-full text-left px-3 py-2 transition-colors duration-150 flex items-center gap-2"
                        style={{
                          background: isActive ? 'var(--ocean-50)' : 'transparent',
                        }}
                        onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = 'hsl(var(--muted))'; }}
                        onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = 'transparent'; }}
                      >
                        <ModelIcon modelKey={model.key} size={15} />
                        <div className="min-w-0">
                          <div className="font-medium text-[11px]" style={{ color: isActive ? 'var(--ocean-500)' : 'hsl(var(--foreground))' }}>
                            {model.label}
                          </div>
                          {model.description && (
                            <div className="text-[9px]" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.35 }}>{model.description}</div>
                          )}
                        </div>
                        {isActive && (
                          <div className="ml-auto w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: 'var(--ocean-500)' }} />
                        )}
                      </button>
                    );
                  })}
                </div>
              </>
            )}
          </div>

          <button
            onClick={toggleReasoning}
            disabled={!currentModel?.supports_reasoning}
            className={`flex items-center gap-1 px-1.5 py-0.5 rounded-md border transition-all duration-200 text-[11px] active:scale-[0.96] ${
              !currentModel?.supports_reasoning ? "opacity-20 cursor-not-allowed" : "cursor-pointer"
            }`}
            style={{
              background: config.enable_reasoning ? 'var(--ocean-50)' : 'transparent',
              borderColor: config.enable_reasoning ? 'var(--ocean-200)' : 'hsl(var(--border))',
              color: config.enable_reasoning ? 'var(--ocean-500)' : 'hsl(var(--muted-foreground))',
              opacity: config.enable_reasoning ? 1 : (!currentModel?.supports_reasoning ? 0.2 : 0.35),
            }}
            title="深度思考"
          >
            <Brain size={10} strokeWidth={1.8} />
            <span>思考</span>
          </button>

          <button
            onClick={toggleSearch}
            className="flex items-center gap-1 px-1.5 py-0.5 rounded-md border transition-all duration-200 text-[11px] cursor-pointer active:scale-[0.96]"
            style={{
              background: config.enable_search ? 'var(--ocean-50)' : 'transparent',
              borderColor: config.enable_search ? 'var(--ocean-200)' : 'hsl(var(--border))',
              color: config.enable_search ? 'var(--ocean-500)' : 'hsl(var(--muted-foreground))',
              opacity: config.enable_search ? 1 : 0.35,
            }}
            title="联网搜索"
          >
            <Globe size={10} strokeWidth={1.8} />
            <span>搜索</span>
          </button>

          <button
            onClick={toggleRag}
            className="flex items-center gap-1 px-1.5 py-0.5 rounded-md border transition-all duration-200 text-[11px] cursor-pointer active:scale-[0.96]"
            style={{
              background: config.enable_rag ? 'var(--teal-50)' : 'transparent',
              borderColor: config.enable_rag ? 'var(--teal-200)' : 'hsl(var(--border))',
              color: config.enable_rag ? 'var(--teal-500)' : 'hsl(var(--muted-foreground))',
              opacity: config.enable_rag ? 1 : 0.35,
            }}
            title="知识库检索"
          >
            <Database size={10} strokeWidth={1.8} />
            <span>RAG</span>
          </button>

          <span className={`${isMobile ? 'hidden' : ''} ml-auto text-[9px] select-none tracking-wide`} style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.22 }}>
            Enter 发送 · Shift+Enter 换行
          </span>
        </div>
      </div>
    </div>
  );
}

function ChevronUp({ size, className, style }: { size: number; className?: string; style?: React.CSSProperties }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className={className} style={style}>
      <path d="m18 15-6-6-6 6" />
    </svg>
  );
}