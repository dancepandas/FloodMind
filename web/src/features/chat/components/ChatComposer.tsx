import { useState, useRef } from "react";
import { Paperclip, Pause, Send, ChevronUp, Brain, Globe, Database } from "lucide-react";
import type { ModelOption, SessionConfig } from "@/types/app";

const MODEL_ICON_MAP: Record<string, string> = {
  qwen_35_plus: "qwen",
  qwen_36_plus: "qwen",
  glm_51: "zhipu",
  glm_5: "zhipu",
  glm_47: "zhipu",
  glm_46: "zhipu",
  glm_45: "zhipu",
  deepseek_v4_pro: "deepseek",
  deepseek_v4_flash: "deepseek",
  kimi_k2_thinking: "kimi",
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
  models: ModelOption[];
  config: SessionConfig;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onPause: () => void;
  onUpload: (file: File) => void;
  onConfigChange: (config: SessionConfig) => void;
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
  onConfigChange,
}: ChatComposerProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);

  const currentModel = models.find((m) => m.key === config.model_key);

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

  const sendBtnClass = isRunning
    ? "bg-amber-500 text-white shadow-[0_2px_8px_-2px_rgba(245,158,11,0.3)] hover:bg-amber-600"
    : value.trim().length > 0 && !disabled
    ? "bg-primary text-primary-foreground shadow-[0_2px_8px_-2px_rgba(59,107,208,0.3)] hover:bg-primary/90"
    : "bg-muted/60 text-muted-foreground/40";

  return (
    <div className="px-4 pt-3 pb-4 bg-gradient-to-t from-background via-background/95 to-background/80 backdrop-blur-lg border-t border-border/40">
      <div className="max-w-3xl mx-auto">
        <div className="relative flex items-end bg-card border border-border/80 rounded-2xl focus-within:ring-2 focus-within:ring-primary/20 focus-within:border-primary/50 transition-all duration-300 shadow-[0_2px_16px_-4px_rgba(0,0,0,0.06)] focus-within:shadow-[0_4px_20px_-4px_rgba(59,107,208,0.1)]">
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={isRunning}
            className="p-3 text-muted-foreground/50 hover:text-foreground transition-colors duration-200 h-[56px] flex items-center justify-center flex-shrink-0 rounded-l-2xl disabled:opacity-40 hover:bg-muted/20"
          >
            <Paperclip size={18} strokeWidth={1.8} />
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
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              if (isRunning) return;
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onSubmit();
              }
            }}
            placeholder="给 FloodMind 发送消息..."
            className="flex-1 max-h-[240px] min-h-[56px] py-4 px-2 bg-transparent resize-none outline-none text-[15px] leading-relaxed text-foreground placeholder:text-muted-foreground/40"
            rows={1}
            disabled={disabled || isRunning}
          />

          <div className="flex items-center gap-1 p-2 h-[56px] flex-shrink-0 rounded-r-2xl">
            <button
              type="button"
              onClick={isRunning ? onPause : onSubmit}
              disabled={!isRunning && (disabled || !value.trim())}
              className={`p-2.5 rounded-xl flex items-center justify-center transition-all duration-250 active:scale-[0.94] ${sendBtnClass}`}
            >
              {isRunning ? <Pause size={15} /> : <Send size={15} className={value.trim().length > 0 ? "ml-0.5" : ""} />}
            </button>
          </div>
        </div>

        <div className="flex items-center gap-1.5 mt-2.5 px-1">
          <div className="relative">
            <button
              onClick={() => setModelMenuOpen(!modelMenuOpen)}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-muted/30 hover:bg-muted/60 border border-border/30 transition-all duration-200 text-[12px]"
            >
              <ModelIcon modelKey={config.model_key} size={14} />
              <span className="font-medium text-foreground/90 truncate max-w-[140px]">
                {currentModel?.label || config.model_key}
              </span>
              <ChevronUp size={11} className={`text-muted-foreground/60 transition-transform duration-250 ${modelMenuOpen ? "rotate-180" : ""}`} />
            </button>

            {modelMenuOpen && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setModelMenuOpen(false)} />
                <div className="absolute bottom-full left-0 mb-1.5 z-50 min-w-[260px] bg-popover border border-border/60 rounded-xl shadow-[0_12px_32px_-8px_rgba(0,0,0,0.15)] py-1 overflow-hidden backdrop-blur-lg">
                  {models.map((model) => {
                    const isActive = model.key === config.model_key;
                    return (
                      <button
                        key={model.key}
                        onClick={() => selectModel(model)}
                        className={`w-full text-left px-3.5 py-2.5 hover:bg-muted/40 transition-colors duration-150 flex items-center gap-2.5 ${isActive ? "bg-primary/[0.06]" : ""}`}
                      >
                        <ModelIcon modelKey={model.key} size={16} />
                        <div className="min-w-0">
                          <div className={`font-medium text-[12px] ${isActive ? "text-primary" : "text-foreground"}`}>{model.label}</div>
                          {model.description && (
                            <div className="text-[10px] text-muted-foreground/50 mt-0.5">{model.description}</div>
                          )}
                        </div>
                        {isActive && (
                          <div className="ml-auto w-1.5 h-1.5 rounded-full bg-primary flex-shrink-0" />
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
            className={`flex items-center gap-1 px-2 py-1 rounded-lg border transition-all duration-200 text-[12px] active:scale-[0.96] ${
              config.enable_reasoning
                ? "bg-primary/10 border-primary/20 text-primary"
                : "bg-transparent border-border/20 text-muted-foreground/40 hover:text-muted-foreground/65 hover:border-border/35"
            } ${!currentModel?.supports_reasoning ? "opacity-25 cursor-not-allowed" : "cursor-pointer"}`}
            title="深度思考"
          >
            <Brain size={11} strokeWidth={1.8} />
            <span>思考</span>
          </button>

          <button
            onClick={toggleSearch}
            className={`flex items-center gap-1 px-2 py-1 rounded-lg border transition-all duration-200 text-[12px] cursor-pointer active:scale-[0.96] ${
              config.enable_search
                ? "bg-primary/10 border-primary/20 text-primary"
                : "bg-transparent border-border/20 text-muted-foreground/40 hover:text-muted-foreground/65 hover:border-border/35"
            }`}
            title="联网搜索"
          >
            <Globe size={11} strokeWidth={1.8} />
            <span>搜索</span>
          </button>

          <button
            onClick={toggleRag}
            className={`flex items-center gap-1 px-2 py-1 rounded-lg border transition-all duration-200 text-[12px] cursor-pointer active:scale-[0.96] ${
              config.enable_rag
                ? "bg-primary/10 border-primary/20 text-primary"
                : "bg-transparent border-border/20 text-muted-foreground/40 hover:text-muted-foreground/65 hover:border-border/35"
            }`}
            title="知识库检索"
          >
            <Database size={11} strokeWidth={1.8} />
            <span>RAG</span>
          </button>

          <span className="ml-auto text-[10px] text-muted-foreground/30 select-none tracking-wide">AI 可能会犯错，请核实重要信息</span>
        </div>
      </div>
    </div>
  );
}
