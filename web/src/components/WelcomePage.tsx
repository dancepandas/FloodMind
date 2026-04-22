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
      <div className="w-full max-w-3xl flex flex-col items-center gap-10">
        <div className="flex flex-col items-center gap-4">
          <div className="w-16 h-16 rounded-2xl bg-primary flex items-center justify-center text-primary-foreground shadow-lg shadow-primary/25">
            <Bot size={36} />
          </div>
          <div className="text-center">
            <h1 className="text-3xl font-bold text-foreground tracking-tight">
              FloodMind
            </h1>
            <p className="mt-2 text-muted-foreground text-base">
              智能洪水预报助手 — 对话即可完成预报、分析与报告
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 w-full">
          {features.map(({ icon: Icon, title, description }) => (
            <div
              key={title}
              className="group flex flex-col gap-2.5 p-4 rounded-xl bg-card border border-border hover:border-primary/30 hover:shadow-sm transition-all"
            >
              <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center text-primary group-hover:bg-primary/15 transition-colors">
                <Icon size={18} />
              </div>
              <div>
                <div className="text-sm font-semibold text-foreground">{title}</div>
                <div className="text-xs text-muted-foreground mt-0.5 leading-relaxed">{description}</div>
              </div>
            </div>
          ))}
        </div>

        <div className="flex flex-col items-center gap-3 w-full">
          <span className="text-xs text-muted-foreground font-medium tracking-wide">
            快速开始
          </span>
          <div className="flex flex-wrap justify-center gap-2">
            {quickActions.map((action) => (
              <button
                key={action}
                onClick={() => onQuickAction?.(action)}
                className="px-4 py-2 text-sm rounded-full bg-secondary/80 text-secondary-foreground border border-border hover:bg-primary hover:text-primary-foreground hover:border-primary transition-all cursor-pointer"
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
