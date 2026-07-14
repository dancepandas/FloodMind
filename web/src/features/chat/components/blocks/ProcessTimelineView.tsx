import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage as ChatMessageModel, MessageBlock } from "@/types/app";
import { ThoughtBlock } from "./ThoughtBlock";
import { ActionBlock } from "./ActionBlock";
import { ErrorBlock } from "./ErrorBlock";

interface ProcessTimelineViewProps {
  blocks: MessageBlock[];
  message: ChatMessageModel;
  onToggleThought: (messageId: string, blockId: string) => void;
}

/** 完成消息的"中间过程"时间线：把 thought/action 渲染为折叠块，answer 截断预览。 */
export function ProcessTimelineView({ blocks, message, onToggleThought }: ProcessTimelineViewProps) {
  let stepNum = 1;

  return (
    <div className="flex flex-col gap-1">
      {blocks.map((block) => {
        if (block.type === "thought") {
          const sn = stepNum++;
          return <ThoughtBlock key={block.id} message={message} block={{ ...block, isCollapsed: true }} onToggleThought={onToggleThought} stepIndex={sn} />;
        }
        if (block.type === "action") {
          const sn = stepNum++;
          return <ActionBlock key={block.id} block={{ ...block, isCollapsed: true }} onToggleThought={onToggleThought} message={message} stepIndex={sn} />;
        }
        if (block.type === "error") {
          return <ErrorBlock key={block.id} content={block.content} />;
        }
        return (
          <div key={block.id} className="px-3 py-2 rounded-lg text-[11px] leading-relaxed" style={{ background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-tertiary)", opacity: 0.75 }}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.content.length > 200 ? block.content.slice(0, 200) + "…" : block.content}</ReactMarkdown>
          </div>
        );
      })}
    </div>
  );
}
