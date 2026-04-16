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
    <div className="p-4 bg-background border-t border-border">
      <div className="max-w-3xl mx-auto relative">
        <div className="relative flex items-end bg-background border border-border shadow-sm rounded-2xl focus-within:ring-2 focus-within:ring-primary/20 focus-within:border-primary transition-all">
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={isRunning}
            className="p-3 text-muted-foreground hover:text-foreground transition-colors h-[52px] flex items-center justify-center flex-shrink-0 rounded-l-2xl disabled:opacity-50"
          >
            <Paperclip size={20} />
          </button>
          <input
            ref={inputRef}
            type="file"
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
            className="flex-1 max-h-[200px] min-h-[52px] py-3.5 px-2 bg-transparent resize-none outline-none text-sm text-foreground placeholder:text-muted-foreground"
            rows={1}
            disabled={disabled || isRunning}
          />

          <div className="flex items-center gap-1 p-2 h-[52px] flex-shrink-0 rounded-r-2xl">
            <button
              type="button"
              onClick={isRunning ? onPause : onSubmit}
              disabled={!isRunning && (disabled || !value.trim())}
              className={`p-2 rounded-full flex items-center justify-center transition-all ${
                isRunning
                  ? "bg-amber-500 text-white shadow-md hover:bg-amber-600"
                  : value.trim().length > 0 && !disabled
                  ? "bg-primary text-primary-foreground shadow-md hover:bg-primary/90"
                  : "bg-muted text-muted-foreground"
              }`}
            >
              {isRunning ? <Pause size={16} /> : <Send size={16} className={value.trim().length > 0 ? "ml-0.5" : ""} />}
            </button>
          </div>
        </div>
        <div className="text-center mt-2">
          <span className="text-[11px] text-muted-foreground">AI Agent 可能会犯错。请核实重要信息。</span>
        </div>
      </div>
    </div>
  );
}
