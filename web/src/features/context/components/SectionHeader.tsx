import { ChevronDown } from "lucide-react";

/**
 * 右侧面板通用的可折叠区域标题栏。
 *
 * 原先在 RightPanel.tsx 和 WorkspaceSelector.tsx 中各有一份私有副本，
 * 已合并到此共享组件，保证样式与交互统一。
 */
export function SectionHeader({
  title,
  icon: Icon,
  expanded,
  onToggle,
  badge,
}: {
  title: string;
  icon: React.ElementType;
  expanded: boolean;
  onToggle: () => void;
  badge?: React.ReactNode;
}) {
  return (
    <button
      onClick={onToggle}
      className="w-full flex items-center gap-2 px-4 py-3 text-left transition-colors hover:bg-black/[0.02] dark:hover:bg-white/[0.02]"
      style={{ color: "var(--text-secondary)" }}
    >
      <Icon size={14} strokeWidth={1.8} />
      <span className="text-[12px] font-bold">{title}</span>
      <div className="ml-auto flex items-center gap-1.5">
        {badge}
        <ChevronDown
          size={14}
          strokeWidth={1.8}
          className={`transition-transform duration-200 ${expanded ? "" : "-rotate-90"}`}
          style={{ color: "var(--text-tertiary)" }}
        />
      </div>
    </button>
  );
}
