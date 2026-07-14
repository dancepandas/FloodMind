import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { parseTaskList } from "@/features/chat/lib/parse-task-list";
import { CheckboxGroupList } from "@/features/chat/components/CheckboxGroupList";

interface MarkdownContentProps {
  content: string;
  onQuickSubmit?: (text: string) => void;
}

/** Markdown 渲染：支持交互式任务清单（checkbox 分组）。流式时按 content 缓存分段。 */
export function MarkdownContent({ content, onQuickSubmit }: MarkdownContentProps) {
  // 流式时 content 每次变化都会重解析；用 useMemo 按 content 缓存分段结果，避免重复 parse。
  const segments = useMemo(() => parseTaskList(content), [content]);

  if (segments.length === 0) return null;
  if (segments.every((s) => s.type === "text")) {
    return (
      <div className="prose prose-sm max-w-none" style={{ color: "var(--text-primary)" }}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      </div>
    );
  }

  const soloTexts: { index: number; content: string }[] = [];
  const checkboxGroups: { label: string; items: import("@/features/chat/lib/parse-task-list").CheckboxItem[] }[] = [];

  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    if (seg.type === "checkbox") {
      const prevText = i > 0 && segments[i - 1].type === "text" ? segments[i - 1].content : "";
      checkboxGroups.push({ label: prevText, items: seg.items || [] });
    }
  }

  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    if (seg.type === "text") {
      const nextIsCheckbox = i + 1 < segments.length && segments[i + 1].type === "checkbox";
      if (!nextIsCheckbox) {
        soloTexts.push({ index: i, content: seg.content });
      }
    }
  }

  return (
    <div className="flex flex-col gap-1" style={{ color: "var(--text-primary)" }}>
      {soloTexts.map((t) => (
        <div key={t.index} className="prose prose-sm max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{t.content}</ReactMarkdown>
        </div>
      ))}
      {checkboxGroups.length > 0 && (
        <CheckboxGroupList groups={checkboxGroups} onSubmit={onQuickSubmit} />
      )}
    </div>
  );
}
