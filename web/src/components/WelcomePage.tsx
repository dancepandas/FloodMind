import { CloudRain, BarChart3, FileText, ArrowRight, Waves, Cpu, Database, Zap } from "lucide-react";

interface WelcomePageProps {
  onQuickAction?: (text: string) => void;
}

const taskCards = [
  {
    icon: CloudRain,
    title: "运行洪水预报",
    description: "上传雨量或水位数据，自动调用模型脚本，输出预报结果与过程线",
    action: "帮我运行一次洪水预报",
    accent: "primary",
    gradient: "from-primary/8 via-primary/4 to-transparent",
    borderHover: "hover:border-primary/30",
    iconBg: "bg-primary/8 text-primary",
    arrowColor: "text-primary/50 group-hover:text-primary",
  },
  {
    icon: BarChart3,
    title: "分析水文数据",
    description: "识别数据异常、统计过程线特征、生成可视化图表与对比分析",
    action: "分析当前流域的降雨数据",
    accent: "emerald",
    gradient: "from-emerald-600/8 via-emerald-600/4 to-transparent",
    borderHover: "hover:border-emerald-600/30",
    iconBg: "bg-emerald-600/8 text-emerald-600",
    arrowColor: "text-emerald-600/50 group-hover:text-emerald-600",
  },
  {
    icon: FileText,
    title: "生成专业报告",
    description: "汇总预报结论、图表和参数，一键输出 Word / PDF 格式报告",
    action: "生成洪水预报报告",
    accent: "amber",
    gradient: "from-amber-600/8 via-amber-600/4 to-transparent",
    borderHover: "hover:border-amber-600/30",
    iconBg: "bg-amber-600/8 text-amber-600",
    arrowColor: "text-amber-600/50 group-hover:text-amber-600",
  },
];

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

      <div className="w-full max-w-2xl flex flex-col items-center gap-10 relative z-10 animate-fade-in-up">
        <div className="flex flex-col items-center gap-4">
          <div className="relative">
            <div className="w-14 h-14 rounded-xl bg-gradient-to-br from-primary/12 to-primary/3 flex items-center justify-center border border-primary/10 shadow-[0_8px_24px_-6px_rgba(38,92,178,0.12)]">
              <img src="/floodmind-icon.svg" alt="FloodMind" className="w-8 h-8" style={{ filter: "drop-shadow(0 1px 2px rgba(38,92,178,0.15))" }} />
            </div>
            <div className="absolute -top-0.5 -right-0.5 w-3 h-3 rounded-full bg-emerald-500 border-2 border-background">
              <div className="w-full h-full rounded-full bg-emerald-500 animate-ping opacity-30" />
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

        <div className="flex flex-col gap-2.5 w-full">
          <div className="px-1 mb-1">
            <span className="text-[10px] font-semibold text-muted-foreground/40 tracking-[0.14em] uppercase">
              任务启动
            </span>
          </div>
          {taskCards.map(({ icon: Icon, title, description, action, gradient, borderHover, iconBg, arrowColor }, i) => (
            <button
              key={title}
              onClick={() => onQuickAction?.(action)}
              className={`group relative w-full flex items-start gap-4 p-4 rounded-xl bg-card border border-border/50 ${borderHover} transition-all duration-300 hover:shadow-[0_4px_20px_-4px_rgba(0,0,0,0.06)] text-left cursor-pointer active:scale-[0.995]`}
              style={{ animationDelay: `${i * 80}ms` }}
            >
              <div className={`absolute inset-0 rounded-xl bg-gradient-to-r ${gradient} opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none`} />
              <div className={`relative w-10 h-10 rounded-lg ${iconBg} flex items-center justify-center flex-shrink-0`}>
                <Icon size={18} strokeWidth={1.6} />
              </div>
              <div className="relative flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-[13px] font-semibold text-foreground tracking-tight">{title}</span>
                  <ArrowRight size={12} className={`${arrowColor} transition-all duration-300 group-hover:translate-x-0.5`} strokeWidth={2} />
                </div>
                <p className="text-[11px] text-muted-foreground/60 mt-1 leading-relaxed">{description}</p>
              </div>
            </button>
          ))}
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
                className="px-3 py-1.5 text-[12px] rounded-lg bg-secondary/60 text-muted-foreground/75 border border-border/40 hover:bg-primary/8 hover:text-primary hover:border-primary/20 transition-all duration-250 cursor-pointer active:scale-[0.97] font-medium"
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
