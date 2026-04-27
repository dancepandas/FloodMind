import { Paperclip, Pause, Send } from "lucide-react";
import { useRef } from "react";

interface ChatComposerProps {
  value: string;
  disabled?: boolean;
  isRunning?: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onPause: () => void;
  onUpload: (file: File) => void;
}

export function ChatComposer({ value, disabled, isRunning, onChange, onSubmit, onPause, onUpload }: ChatComposerProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);

  return (
    <div className="p-4 bg-background/80 backdrop-blur-md border-t border-border/60">
      <div className="max-w-3xl mx-auto relative">
        <div className="relative flex items-end bg-card border border-border rounded-2xl focus-within:ring-2 focus-within:ring-primary/15 focus-within:border-primary/40 transition-all duration-200 shadow-[0_2px_8px_-2px_rgba(0,0,0,0.04)]">
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={isRunning}
            className="p-3 text-muted-foreground/70 hover:text-foreground transition-colors duration-150 h-[52px] flex items-center justify-center flex-shrink-0 rounded-l-2xl disabled:opacity-40"
          >
            <Paperclip size={19} />
          </button>
          <input
            ref={inputRef}
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
                if (isRunning) {
                  return;
                }
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  onSubmit();
                }
            }}
            placeholder="给 Agent 发送消息..."
            className="flex-1 max-h-[200px] min-h-[52px] py-3.5 px-2 bg-transparent resize-none outline-none text-sm text-foreground placeholder:text-muted-foreground/60"
            rows={1}
            disabled={disabled || isRunning}
          />

          <div className="flex items-center gap-1 p-2 h-[52px] flex-shrink-0 rounded-r-2xl">
            <button
              type="button"
              onClick={isRunning ? onPause : onSubmit}
              disabled={!isRunning && (disabled || !value.trim())}
              className={`p-2.5 rounded-xl flex items-center justify-center transition-all duration-200 active:scale-[0.96] ${
                isRunning
                  ? "bg-amber-500 text-white shadow-sm hover:bg-amber-600"
                  : value.trim().length > 0 && !disabled
                  ? "bg-primary text-primary-foreground shadow-sm hover:bg-primary/90"
                  : "bg-muted text-muted-foreground/50"
              }`}
            >
              {isRunning ? <Pause size={15} /> : <Send size={15} className={value.trim().length > 0 ? "ml-0.5" : ""} />}
            </button>
          </div>
        </div>
        <div className="text-center mt-2">
          <span className="text-[11px] text-muted-foreground/60">AI Agent 可能会犯错。请核实重要信息。</span>
        </div>
      </div>
    </div>
  );
}
