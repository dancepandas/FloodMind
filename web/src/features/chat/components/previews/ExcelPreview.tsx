import { useEffect, useRef, useState } from "react";
import * as XLSX from "xlsx";

interface ExcelPreviewProps {
  url: string;
}

interface SheetData {
  name: string;
  columns: string[];
  rows: string[][];
}

export function ExcelPreview({ url }: ExcelPreviewProps) {
  const [sheets, setSheets] = useState<SheetData[]>([]);
  const [activeSheet, setActiveSheet] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.arrayBuffer();
      })
      .then((buffer) => {
        if (cancelled) return;
        const wb = XLSX.read(buffer, { type: "array" });
        const parsed: SheetData[] = wb.SheetNames.map((name) => {
          const ws = wb.Sheets[name];
          const data: string[][] = XLSX.utils.sheet_to_json(ws, { header: 1, defval: "" });
          const columns = data.length > 0 ? data[0].map(String) : [];
          const rows = data.slice(1).map((row) => row.map(String));
          return { name, columns, rows };
        });
        setSheets(parsed);
        setLoading(false);
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

  if (loading) {
    return <div className="flex items-center justify-center h-full text-muted-foreground text-sm">正在加载表格...</div>;
  }

  if (error) {
    return <div className="flex items-center justify-center h-full text-destructive text-sm">表格加载失败：{error}</div>;
  }

  if (sheets.length === 0) {
    return <div className="flex items-center justify-center h-full text-muted-foreground text-sm">空工作簿</div>;
  }

  const current = sheets[activeSheet];

  return (
    <div className="h-full flex flex-col">
      {sheets.length > 1 && (
        <div className="flex border-b border-border bg-muted/30 overflow-x-auto flex-shrink-0">
          {sheets.map((sheet, idx) => (
            <button
              key={sheet.name}
              onClick={() => setActiveSheet(idx)}
              className={`px-4 py-2 text-sm whitespace-nowrap transition-colors ${
                idx === activeSheet
                  ? "text-primary border-b-2 border-primary font-medium"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {sheet.name}
            </button>
          ))}
        </div>
      )}
      <div className="flex-1 overflow-auto">
        <table className="min-w-full border-collapse text-sm">
          <thead className="sticky top-0 z-10">
            <tr className="bg-muted/80 backdrop-blur-sm">
              <th className="border-b border-r border-border px-3 py-2 text-left font-semibold text-foreground bg-muted/90 w-12">
                #
              </th>
              {current.columns.map((col, i) => (
                <th
                  key={i}
                  className="border-b border-r border-border px-3 py-2 text-left font-semibold text-foreground bg-muted/90 whitespace-nowrap min-w-[80px]"
                >
                  {col || `列${i + 1}`}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {current.rows.map((row, rowIdx) => (
              <tr key={rowIdx} className="hover:bg-muted/30 transition-colors">
                <td className="border-b border-r border-border px-3 py-1.5 text-muted-foreground text-xs bg-muted/20">
                  {rowIdx + 1}
                </td>
                {current.columns.map((_, colIdx) => (
                  <td key={colIdx} className="border-b border-r border-border px-3 py-1.5 whitespace-nowrap">
                    {row[colIdx] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between px-3 py-1.5 border-t border-border bg-muted/20 text-xs text-muted-foreground flex-shrink-0">
        <span>
          {current.rows.length} 行 × {current.columns.length} 列
        </span>
        <span>
          工作表 {activeSheet + 1}/{sheets.length}
        </span>
      </div>
    </div>
  );
}
