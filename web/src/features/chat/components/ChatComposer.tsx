import { useRef, useState, useEffect } from "react";
import { Send, Pause, Paperclip, ChevronDown, Brain, Globe, X } from "lucide-react";
import { useIsMobile } from "@/hooks/use-mobile";
import type { ModelOption, SessionConfig, PendingPermissionAsk, UploadedFileItem } from "@/types/app";

const PINNED_MODELS = ["deepseek_v4_flash", "deepseek_v4_pro", "qwen_36_plus", "qwen_35_plus", "qwen3_6_27b_local", "glm_51", "glm_5", "kimi_k2_5", "kimi_k2_6", "minimax_m25", "minimax_m21"];

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
  deepseek_v4_flash: "deepseek",
  deepseek_v4_pro: "deepseek",
  qwen_36_plus: "qwen",
  qwen_35_plus: "qwen",
  qwen3_6_27b_local: "qwen",
  glm_51: "zhipu",
  glm_5: "zhipu",
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
  pendingFiles: UploadedFileItem[];
  onRemovePendingFile: (fileId: string) => void;
  onConfigChange: (config: SessionConfig) => void;
  pendingPermissionAsk?: PendingPermissionAsk | null;
  onRespondPermissionAsk?: (approved: boolean) => void;
}

export function ChatComposer({
  value,
  disabled,
  isRunning,
  models,
  config,
  onChange,
  onSubmit,
  onPause,
  onUpload,
  pendingFiles,
  onRemovePendingFile,
  onConfigChange,
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

  // 发送按钮始终是“发送”（运行中发送 = 排队）；停止是独立按钮（运行中才出现）
  const canSend = value.trim().length > 0 && !disabled;
  const sendBtnStyle = canSend
    ? { background: "linear-gradient(135deg, var(--wave), var(--reef))", color: "white", boxShadow: "0 2px 12px rgba(14,165,233,0.25)" }
    : { background: "var(--surface-3)", color: "var(--text-tertiary)", opacity: 0.35 };

  return (
    <div className="px-4 pt-2.5 pb-3" style={{ background: "var(--bg)" }}>
      <div className={`${isMobile ? 'w-full' : 'max-w-[780px]'} mx-auto`}>
        {pendingFiles.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-2 px-0.5">
            {pendingFiles.map((file) => (
              <span
                key={file.id}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px]"
                style={{ background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
              >
                <Paperclip size={10} style={{ color: "var(--text-tertiary)" }} />
                <span className="truncate max-w-[160px]">{file.name}</span>
                <button
                  type="button"
                  onClick={() => onRemovePendingFile(file.id)}
                  className="ml-0.5 transition-opacity"
                  style={{ color: "var(--text-tertiary)" }}
                  aria-label="移除文件"
                >
                  <X size={11} />
                </button>
              </span>
            ))}
          </div>
        )}
        <div
          className="relative flex items-end rounded-[var(--radius-prototype)] transition-all duration-300 animate-border-glow"
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            boxShadow: "var(--shadow-md)",
            backdropFilter: "blur(8px)",
          }}
        >
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={isRunning}
            className="p-3 h-[52px] flex items-center justify-center flex-shrink-0 rounded-l-xl transition-all duration-200 disabled:opacity-25"
            style={{ color: "var(--text-tertiary)", opacity: 0.5 }}
            onMouseEnter={(e) => { if (!isRunning) { e.currentTarget.style.color = "var(--wave)"; e.currentTarget.style.opacity = "1"; e.currentTarget.style.background = "var(--accent-light)"; }}}
            onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-tertiary)"; e.currentTarget.style.opacity = "0.5"; e.currentTarget.style.background = "transparent"; }}
          >
            <Paperclip size={16} strokeWidth={1.8} />
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.xlsx,.xls,.txt,.json,.docx,.pdf,.md,.png,.jpg,.jpeg,.webp,.gif,.bmp"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) {
                const imageExts = ['.png','.jpg','.jpeg','.webp','.gif','.bmp'];
                const ext = '.' + file.name.split('.').pop()?.toLowerCase();
                if (imageExts.includes(ext) && currentModel && !currentModel.supports_vision) {
                  alert(`当前模型 ${currentModel.label} 不支持图像理解，请切换至支持视觉的模型后再上传图片。`);
                  event.currentTarget.value = "";
                  return;
                }
                onUpload(file);
              }
              event.currentTarget.value = "";
            }}
          />

          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              // 运行中也可输入并发送（= 追加指令排队），故不拦截 Enter
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onSubmit();
              }
            }}
            placeholder="输入预报任务指令..."
            className="flex-1 max-h-[220px] min-h-[52px] py-3.5 px-2 bg-transparent resize-none outline-none text-[14px] leading-relaxed placeholder:opacity-30"
            style={{ color: "var(--text-primary)", fontFamily: "var(--font-body)" }}
            rows={1}
            disabled={disabled}
          />

          <div className="flex items-center gap-0.5 p-1.5 h-[52px] flex-shrink-0 rounded-r-xl">
            {isRunning && (
              <button
                type="button"
                onClick={onPause}
                title="停止（中止当前任务）"
                className="p-2.5 rounded-xl flex items-center justify-center transition-all duration-300 active:scale-[0.92]"
                style={{ background: "var(--sand)", color: "white", boxShadow: "0 2px 10px rgba(245,158,11,0.3)" }}
              >
                <Pause size={14} />
              </button>
            )}
            <button
              type="button"
              onClick={onSubmit}
              disabled={!canSend}
              title={isRunning ? "追加指令（排队到下一次 LLM 调用）" : "发送"}
              className="p-2.5 rounded-xl flex items-center justify-center transition-all duration-300 active:scale-[0.92]"
              style={sendBtnStyle}
            >
              <Send size={14} className={value.trim().length > 0 ? "ml-0.5" : ""} />
            </button>
          </div>
        </div>

        <div className="flex items-center gap-1.5 mt-2 px-0.5">
          <div className="relative">
            <button
              onClick={() => setModelMenuOpen(!modelMenuOpen)}
              className="flex items-center gap-1.5 px-2 py-0.5 rounded-md transition-all duration-200 text-[11px]"
              style={{
                background: "var(--surface-2)",
                border: "1px solid var(--border)",
                opacity: modelMenuOpen ? 1 : 0.7,
              }}
            >
              <ModelIcon modelKey={config.model_key} size={13} />
              <span className="font-medium truncate max-w-[120px]" style={{ color: "var(--text-primary)", opacity: 0.9 }}>
                {currentModel?.label || config.model_key}
              </span>
              <ChevronUp size={10} className={`transition-transform duration-250 ${modelMenuOpen ? "rotate-180" : ""}`} style={{ color: "var(--text-tertiary)", opacity: 0.6 }} />
            </button>

            {modelMenuOpen && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setModelMenuOpen(false)} />
                <div
                  className="absolute bottom-full left-0 mb-1.5 z-50 min-w-[240px] py-0.5 max-h-[230px] overflow-y-auto rounded-xl"
                  style={{
                    background: "var(--surface)",
                    border: "1px solid var(--border)",
                    boxShadow: "var(--shadow-md)",
                    backdropFilter: "blur(8px)",
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
                          background: isActive ? "var(--accent-light)" : "transparent",
                        }}
                        onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = "var(--surface-2)"; }}
                        onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = "transparent"; }}
                      >
                        <ModelIcon modelKey={model.key} size={15} />
                        <div className="min-w-0">
                          <div className="font-medium text-[11px]" style={{ color: isActive ? "var(--wave)" : "var(--text-primary)" }}>
                            {model.label}
                          </div>
                          {model.description && (
                            <div className="text-[9px]" style={{ color: "var(--text-tertiary)" }}>{model.description}</div>
                          )}
                        </div>
                        {isActive && (
                          <div className="ml-auto w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: "var(--wave)" }} />
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
              background: config.enable_reasoning ? "var(--accent-light)" : "transparent",
              borderColor: config.enable_reasoning ? "var(--wave)" : "var(--border)",
              color: config.enable_reasoning ? "var(--wave)" : "var(--text-secondary)",
              opacity: config.enable_reasoning ? 1 : (!currentModel?.supports_reasoning ? 0.2 : 0.55),
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
              background: config.enable_search ? "var(--accent-light)" : "transparent",
              borderColor: config.enable_search ? "var(--wave)" : "var(--border)",
              color: config.enable_search ? "var(--wave)" : "var(--text-secondary)",
              opacity: config.enable_search ? 1 : 0.55,
            }}
            title="联网搜索"
          >
            <Globe size={10} strokeWidth={1.8} />
            <span>搜索</span>
          </button>

          <span className={`${isMobile ? 'hidden' : ''} ml-auto text-[9px] select-none tracking-wide`} style={{ color: "var(--text-tertiary)", opacity: 0.55 }}>
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
