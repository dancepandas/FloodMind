import { Bot, CloudRain, BarChart3, FileText, Zap, Search, Brain } from "lucide-react";

interface WelcomePageProps {
  onQuickAction?: (text: string) => void;
}

const features = [
  {
    icon: CloudRain,
    title: "洪水预报",
    description: "基于多模型融合的智能洪水预报，支持多种水文模型",
  },
  {
    icon: BarChart3,
    title: "数据分析",
    description: "自动处理水文数据，生成可视化图表与分析报告",
  },
  {
    icon: FileText,
    title: "报告生成",
    description: "一键生成 Word/PDF 格式的专业洪水预报报告",
  },
  {
    icon: Search,
    title: "知识检索",
    description: "RAG 驱动的水文领域知识库，精准检索专业资料",
  },
  {
    icon: Brain,
    title: "深度推理",
    description: "支持推理模式，对复杂水文问题进行深度分析",
  },
  {
    icon: Zap,
    title: "工具调用",
    description: "自动调度计算脚本与数据处理工具，高效执行任务",
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
      <div className="w-full max-w-3xl flex flex-col items-center gap-12">
        <div className="flex flex-col items-center gap-5">
          <div className="w-14 h-14 rounded-2xl bg-primary/8 flex items-center justify-center text-primary">
            <Bot size={28} />
          </div>
          <div className="text-center">
            <h1 className="text-2xl font-semibold text-foreground tracking-tight">
              FloodMind
            </h1>
            <p className="mt-2 text-muted-foreground text-sm leading-relaxed max-w-[50ch]">
              智能洪水预报助手 — 对话即可完成预报、分析与报告
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 w-full">
          {features.map(({ icon: Icon, title, description }) => (
            <div
              key={title}
              className="group flex flex-col gap-2.5 p-4 rounded-xl bg-card border border-border/60 hover:border-primary/20 transition-all duration-200"
            >
              <div className="w-8 h-8 rounded-lg bg-primary/6 flex items-center justify-center text-primary group-hover:bg-primary/10 transition-colors duration-200">
                <Icon size={16} />
              </div>
              <div>
                <div className="text-[13px] font-semibold text-foreground">{title}</div>
                <div className="text-[11px] text-muted-foreground/70 mt-0.5 leading-relaxed">{description}</div>
              </div>
            </div>
          ))}
        </div>

        <div className="flex flex-col items-center gap-3 w-full">
          <span className="text-[11px] text-muted-foreground/50 font-medium tracking-widest uppercase">
            快速开始
          </span>
          <div className="flex flex-wrap justify-center gap-2">
            {quickActions.map((action) => (
              <button
                key={action}
                onClick={() => onQuickAction?.(action)}
                className="px-4 py-2 text-[13px] rounded-full bg-secondary/60 text-secondary-foreground border border-border/60 hover:bg-primary hover:text-primary-foreground hover:border-primary transition-all duration-200 cursor-pointer active:scale-[0.97]"
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
