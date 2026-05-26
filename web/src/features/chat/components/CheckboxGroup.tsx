import { useState, useCallback } from "react";
import type { CheckboxItem } from "@/features/chat/lib/parse-task-list";

interface CheckboxGroupProps {
  items: CheckboxItem[];
  onSubmit?: (reply: string) => void;
}

export function CheckboxGroup({ items, onSubmit }: CheckboxGroupProps) {
  const [selected, setSelected] = useState<boolean[]>(() => items.map((item) => item.checked));

  const toggle = useCallback((index: number) => {
    setSelected((prev) => {
      const next = [...prev];
      next[index] = !next[index];
      return next;
    });
  }, []);

  const handleSubmit = useCallback(() => {
    if (!onSubmit) return;
    const checkedTexts = items
      .filter((_, i) => selected[i])
      .map((item) => item.text)
      .filter(Boolean);
    if (checkedTexts.length === 0) return;
    onSubmit(`选择：${checkedTexts.join("、")}`);
  }, [items, selected, onSubmit]);

  const hasSelection = selected.some(Boolean);

  return (
    <div
      className="my-3 rounded-xl overflow-hidden"
      style={{
        background: 'var(--card)',
        border: '1px solid hsl(var(--border))',
        boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
      }}
    >
      <div className="flex flex-col gap-0.5 px-1 py-1">
        {items.map((item, index) => (
          <label
            key={index}
            className="checkbox-container gap-2.5 px-3 py-2 rounded-lg transition-colors duration-150 select-none hover:bg-[hsl(var(--muted))] active:scale-[0.99]"
            style={{
              background: selected[index] ? 'hsl(var(--muted))' : 'transparent',
            }}
          >
            <input type="checkbox" checked={selected[index]} onChange={() => toggle(index)} />
            <span className="checkmark" />
            <span
              className="text-[13px] leading-relaxed"
              style={{
                color: 'hsl(var(--foreground))',
                opacity: selected[index] ? 1 : 0.65,
              }}
            >
              {item.text}
            </span>
          </label>
        ))}
      </div>

      {onSubmit && hasSelection && (
        <div
          className="flex items-center justify-between px-3 py-2"
          style={{ borderTop: '1px solid hsl(var(--border))' }}
        >
          <span className="text-[11px]" style={{ color: 'hsl(var(--muted-foreground))' }}>
            已选 {selected.filter(Boolean).length} 项
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
