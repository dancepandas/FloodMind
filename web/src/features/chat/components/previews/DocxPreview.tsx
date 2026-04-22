import { useEffect, useRef, useState } from "react";
import { renderAsync } from "docx-preview";

interface DocxPreviewProps {
  url: string;
}

export function DocxPreview({ url }: DocxPreviewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    let cancelled = false;

    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.arrayBuffer();
      })
      .then((buffer) => {
        if (cancelled) return;
        return renderAsync(buffer, containerRef.current!, undefined, {
          className: "docx-preview-wrapper",
          inWrapper: true,
          ignoreWidth: false,
          ignoreHeight: false,
          ignoreFonts: false,
          breakPages: true,
          ignoreLastRenderedPageBreak: true,
          experimental: true,
          trimXmlDeclaration: true,
          renderHeaders: true,
          renderFooters: true,
          renderFootnotes: true,
        });
      })
      .then(() => {
        if (!cancelled) setLoading(false);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message);
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [url]);

  if (error) {
    return <div className="flex items-center justify-center h-full text-destructive text-sm">文档加载失败：{error}</div>;
  }

  return (
    <div className="h-full overflow-auto bg-gray-100">
      {loading && (
        <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">正在加载文档...</div>
      )}
      <div ref={containerRef} />
    </div>
  );
}
