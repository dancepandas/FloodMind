import { useState, useCallback, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { CheckboxItem } from "@/features/chat/lib/parse-task-list";

interface CheckboxGroupItem {
  label: string;
  items: CheckboxItem[];
}

interface CheckboxGroupListProps {
  groups: CheckboxGroupItem[];
  onSubmit?: (reply: string) => void;
}

export function CheckboxGroupList({ groups, onSubmit }: CheckboxGroupListProps) {
  const allItems = useMemo(() => groups.flatMap((g) => g.items), [groups]);
  const groupOffsets = useMemo(() => {
    const offsets: number[] = [];
    let offset = 0;
    for (const g of groups) {
      offsets.push(offset);
      offset += g.items.length;
    }
    return offsets;
  }, [groups]);

  const allChecked = useMemo(() => allItems.map((item) => item.checked), [allItems]);

  const [selected, setSelected] = useState<boolean[]>(() => [...allChecked]);

  const toggle = useCallback((globalIndex: number) => {
    setSelected((prev) => {
      const next = [...prev];
      next[globalIndex] = !next[globalIndex];
      return next;
    });
  }, []);

  const handleSubmit = useCallback(() => {
    if (!onSubmit) return;
    const lines: string[] = [];
    for (let gi = 0; gi < groups.length; gi++) {
      const group = groups[gi];
      const checkedTexts = group.items
        .filter((_, i) => selected[groupOffsets[gi] + i])
        .map((item) => item.text)
        .filter(Boolean);
      if (checkedTexts.length > 0) {
        const label = group.label;
        lines.push(`${label}：${checkedTexts.join("、")}`);
      }
    }
    if (lines.length === 0) return;
    onSubmit(`选择：\n${lines.join("\n")}`);
  }, [groups, selected, groupOffsets, onSubmit]);

  const totalSelected = selected.filter(Boolean).length;

  return (
    <div
      className="my-3 rounded-xl overflow-hidden"
      style={{
        background: 'var(--card)',
        border: '1px solid hsl(var(--border))',
        boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
      }}
    >
      {groups.map((group, gi) => (
        <div key={gi}>
          {group.label && (
            <div className="px-3 pt-2.5 pb-1 prose prose-sm max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{group.label}</ReactMarkdown>
            </div>
          )}
          <div className="flex flex-col gap-0.5 px-1 pb-1">
            {group.items.map((item, ii) => {
              const globalIdx = groupOffsets[gi] + ii;
              const isSel = selected[globalIdx];
              return (
                <label
                  key={ii}
                  className="checkbox-container gap-2.5 px-3 py-2 rounded-lg transition-colors duration-150 select-none hover:bg-[hsl(var(--muted))] active:scale-[0.99]"
                  style={{
                    background: isSel ? 'hsl(var(--muted))' : 'transparent',
                  }}
                >
                  <input type="checkbox" checked={isSel} onChange={() => toggle(globalIdx)} />
                  <span className="checkmark" />
                  <span
                    className="text-[13px] leading-relaxed"
                    style={{
                      color: 'hsl(var(--foreground))',
                      opacity: isSel ? 1 : 0.65,
                    }}
                  >
                    {item.text}
                  </span>
                </label>
              );
            })}
          </div>
        </div>
      ))}

      {onSubmit && totalSelected > 0 && (
        <div
          className="flex items-center justify-between px-3 py-2"
          style={{ borderTop: '1px solid hsl(var(--border))' }}
        >
          <span className="text-[11px]" style={{ color: 'hsl(var(--muted-foreground))' }}>
            已选 {totalSelected} 项
          </span>
          <button
            onClick={handleSubmit}
            className="px-4 py-1.5 rounded-lg text-[12px] font-semibold transition-all duration-200 active:scale-[0.96] hover:brightness-110"
            style={{
              background: 'var(--gradient-ocean-teal)',
              color: '#fff',
              boxShadow: '0 2px 8px rgba(37,99,168,0.25)',
            }}
          >
            提交选择
          </button>
        </div>
      )}
    </div>
  );
}
