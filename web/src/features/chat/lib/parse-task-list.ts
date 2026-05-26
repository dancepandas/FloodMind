import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import type { Root, ListItem, List } from "mdast";

export interface CheckboxItem {
  text: string;
  checked: boolean;
}

export interface ContentSegment {
  type: "text" | "checkbox";
  content: string;
  items?: CheckboxItem[];
}

function extractItemText(node: any): string {
  const parts: string[] = [];
  function walk(n: any) {
    if (n.type === "text" || n.type === "inlineCode") parts.push(n.value || "");
    else if (n.children) n.children.forEach(walk);
  }
  (node.children || []).forEach(walk);
  return parts.join(" ").replace(/\s+/g, " ").trim();
}

export function parseTaskList(markdown: string): ContentSegment[] {
  const segments: ContentSegment[] = [];
  if (!markdown) return segments;

  let root: Root;
  try {
    root = unified().use(remarkParse).use(remarkGfm).parse(markdown) as Root;
  } catch {
    segments.push({ type: "text", content: markdown });
    return segments;
  }

  let lastEnd = 0;

  function flushText(end: number) {
    if (end > lastEnd) {
      const raw = markdown.slice(lastEnd, end);
      if (raw.trim()) segments.push({ type: "text", content: raw });
    }
  }

  for (const node of root.children) {
    if (node.type !== "list") continue;

    const list = node as List;
    const start = node.position?.start?.offset;
    const end = node.position?.end?.offset;
    if (start === undefined || end === undefined) continue;

    const taskItems: CheckboxItem[] = [];
    let hasTaskItems = false;

    for (const child of (list.children || []) as ListItem[]) {
      if (child.checked !== null && child.checked !== undefined) {
        hasTaskItems = true;
        taskItems.push({
          text: extractItemText(child),
          checked: child.checked,
        });
      }
    }

    if (hasTaskItems) {
      let beforeEnd = start;
      for (const child of (list.children || []) as ListItem[]) {
        if (child.checked === null || child.checked === undefined) {
          const childEnd = child.position?.end?.offset;
          if (childEnd && childEnd > beforeEnd) beforeEnd = childEnd;
        }
      }
      flushText(beforeEnd);
      segments.push({ type: "checkbox", content: "", items: taskItems });
      lastEnd = end;
    }
  }

  flushText(markdown.length);
  return segments;
}
