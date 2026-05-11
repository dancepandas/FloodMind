import { CloudRain, BarChart3, FileText, Zap, Search, Brain } from "lucide-react";

interface WelcomePageProps {
  onQuickAction?: (text: string) => void;
}

const features = [
  {
    icon: CloudRain,
    title: "洪水预报",
    description: "多模型融合智能预报，覆盖多种水文模型",
    accent: "from-blue-500/12 to-cyan-500/8",
  },
  {
    icon: BarChart3,
    title: "数据分析",
    description: "自动处理水文数据，生成可视化图表与报告",
    accent: "from-emerald-500/12 to-teal-500/8",
  },
  {
    icon: FileText,
    title: "报告生成",
    description: "一键生成 Word/PDF 格式的专业预报报告",
    accent: "from-violet-500/12 to-purple-500/8",
  },
  {
    icon: Search,
    title: "知识检索",
    description: "RAG 驱动的水文领域知识库，精准检索资料",
    accent: "from-amber-500/12 to-orange-500/8",
  },
  {
    icon: Brain,
    title: "深度推理",
    description: "推理模式对复杂水文问题进行深度分析",
    accent: "from-rose-500/12 to-pink-500/8",
  },
  {
    icon: Zap,
    title: "工具调用",
    description: "自动调度计算脚本与数据处理工具执行任务",
    accent: "from-sky-500/12 to-indigo-500/8",
  },
];

const quickActions = [
  "帮我运行一次洪水预报",
  "分析当前流域的降雨数据",
  "生成洪水预报报告",
  "查询水文模型参数说明",
];

export function WelcomePage({ onQuickAction }: WelcomePageProps) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6 py-10 overflow-y-auto">
      <div className="w-full max-w-3xl flex flex-col items-center gap-14 animate-fade-in-up">
        <div className="flex flex-col items-center gap-5">
          <div className="relative">
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-primary/15 to-primary/5 flex items-center justify-center shadow-[0_8px_24px_-6px_rgba(59,107,208,0.15)]">
              <img src="/floodmind-icon.svg" alt="FloodMind" className="w-9 h-9 text-primary" style={{ filter: "drop-shadow(0 1px 2px rgba(59,107,208,0.2))" }} />
            </div>
            <div className="absolute -top-1 -right-1 w-3.5 h-3.5 rounded-full bg-emerald-400 border-2 border-background shadow-sm">
              <div className="w-full h-full rounded-full bg-emerald-400 animate-ping opacity-40" />
            </div>
          </div>
          <div className="text-center">
            <h1 className="text-[28px] font-bold text-foreground tracking-tight leading-none">
              FloodMind
            </h1>
            <p className="mt-3 text-muted-foreground/80 text-[14px] leading-relaxed max-w-[52ch]">
              智能洪水预报助手 — 对话即可完成预报、分析与报告
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 w-full">
          {features.map(({ icon: Icon, title, description, accent }, i) => (
            <div
              key={title}
              className="group relative flex flex-col gap-3 p-4 rounded-xl bg-card border border-border/50 hover:border-primary/20 transition-all duration-300 hover:shadow-[0_4px_16px_-4px_rgba(0,0,0,0.06)]"
              style={{ animationDelay: `${i * 60}ms` }}
            >
              <div className={`w-9 h-9 rounded-lg bg-gradient-to-br ${accent} flex items-center justify-center text-primary group-hover:scale-105 transition-transform duration-300`}>
                <Icon size={17} strokeWidth={1.8} />
              </div>
              <div>
                <div className="text-[13px] font-semibold text-foreground tracking-tight">{title}</div>
                <div className="text-[11px] text-muted-foreground/65 mt-1 leading-relaxed">{description}</div>
              </div>
            </div>
          ))}
        </div>

        <div className="flex flex-col items-center gap-3.5 w-full">
          <span className="text-[10px] text-muted-foreground/40 font-semibold tracking-[0.15em] uppercase">
            快速开始
          </span>
          <div className="flex flex-wrap justify-center gap-2">
            {quickActions.map((action) => (
              <button
                key={action}
                onClick={() => onQuickAction?.(action)}
                className="px-4 py-2 text-[13px] rounded-full bg-secondary/50 text-secondary-foreground/80 border border-border/50 hover:bg-primary hover:text-primary-foreground hover:border-primary hover:shadow-[0_4px_12px_-2px_rgba(59,107,208,0.2)] transition-all duration-250 cursor-pointer active:scale-[0.97]"
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
