import { Waves, Cpu, Database, Zap } from "lucide-react";

interface WelcomePageProps {
  onQuickAction?: (text: string) => void;
}

const quickActions = [
  "查询水文模型参数说明",
  "对比不同模型预报结果",
  "绘制降雨等值线图",
  "导出站点数据为Excel",
];

const capabilities = [
  { icon: Waves, label: "多模型融合" },
  { icon: Cpu, label: "深度推理" },
  { icon: Database, label: "RAG 知识库" },
  { icon: Zap, label: "工具链执行" },
];

export function WelcomePage({ onQuickAction }: WelcomePageProps) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6 py-8 overflow-y-auto relative">
      <div className="absolute inset-0 hydro-grid-bg opacity-60 pointer-events-none" />
      <div className="absolute inset-x-0 top-0 h-64 hydro-radial-glow pointer-events-none" />

      <div className="w-full max-w-2xl flex flex-col items-center gap-10 relative z-[1] animate-fade-in-up">
        <div className="flex flex-col items-center gap-4">
          <div className="relative">
            <div className="w-14 h-14 rounded-xl bg-sky-500 flex items-center justify-center shadow-[0_8px_24px_-6px_rgba(14,165,233,0.25)]">
              <img src="/floodmind-icon.svg" alt="FloodMind" className="w-8 h-8" style={{ filter: "brightness(0) invert(1)" }} />
            </div>
            <div className="absolute -top-0.5 -right-0.5 w-3 h-3 rounded-full bg-sky-400 border-2 border-background">
              <div className="w-full h-full rounded-full bg-sky-400 animate-ping opacity-30" />
            </div>
          </div>
          <div className="text-center">
            <h1 className="text-2xl font-bold text-foreground tracking-tight leading-none">
              FloodMind
            </h1>
            <p className="mt-2.5 text-muted-foreground text-[13px] leading-relaxed max-w-[48ch]">
              把洪水预报任务交给 Agent 执行 — 规划、计算、分析、制图、报告，端到端完成
            </p>
          </div>
          <div className="flex items-center gap-3 mt-1">
            {capabilities.map(({ icon: Icon, label }) => (
              <div key={label} className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-muted/50 border border-border/40">
                <Icon size={11} className="text-muted-foreground/60" strokeWidth={1.8} />
                <span className="text-[10px] text-muted-foreground/70 font-medium tracking-tight">{label}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="flex flex-col items-center gap-3 w-full">
          <span className="text-[10px] text-muted-foreground/35 font-semibold tracking-[0.14em] uppercase">
            快速指令
          </span>
          <div className="flex flex-wrap justify-center gap-1.5">
            {quickActions.map((action) => (
              <button
                key={action}
                onClick={() => onQuickAction?.(action)}
                className="px-3 py-1.5 text-[12px] rounded-xl bg-secondary/60 text-muted-foreground/75 border border-border/40 hover:bg-sky-50 hover:text-sky-600 hover:border-sky-200/60 transition-all duration-250 cursor-pointer active:scale-[0.97] font-medium"
              >
                {action}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
