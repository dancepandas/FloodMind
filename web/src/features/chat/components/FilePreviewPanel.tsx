import { lazy, Suspense } from "react";
import { Download, X, FileText, FileImage, FileSpreadsheet, FileType, FileCode } from "lucide-react";
import { CanvasTextViewer } from "./previews/CanvasTextViewer";
import { CanvasTableRenderer } from "./previews/CanvasTableRenderer";
import type { FilePreview } from "@/types/app";
import { resolveMediaUrl } from "@/api/client";

const PdfPreview = lazy(() => import("./previews/PdfPreview").then((m) => ({ default: m.PdfPreview })));
const DocxPreview = lazy(() => import("./previews/DocxPreview").then((m) => ({ default: m.DocxPreview })));
const ExcelPreview = lazy(() => import("./previews/ExcelPreview").then((m) => ({ default: m.ExcelPreview })));

interface FilePreviewPanelProps {
  preview: FilePreview;
  onClose: () => void;
}

function getFileExt(filename: string): string {
  return filename.split(".").pop()?.toLowerCase() || "";
}

function formatFileSize(bytes: number): string {
  if (!bytes || bytes <= 0) return "—";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function extCategory(ext: string): { label: string; color: string; Icon: typeof FileText } {
  if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext))
    return { label: "Image", color: "#10b981", Icon: FileImage };
  if (ext === "pdf")
    return { label: "PDF", color: "#ef4444", Icon: FileType };
  if (["xlsx", "xls", "csv", "tsv"].includes(ext))
    return { label: "Spreadsheet", color: "#22c55e", Icon: FileSpreadsheet };
  if (["docx", "doc"].includes(ext))
    return { label: "Document", color: "#3b82f6", Icon: FileText };
  if (["py", "js", "ts", "json", "yaml", "yml", "html", "css", "md", "txt", "log"].includes(ext))
    return { label: "Code", color: "#a855f7", Icon: FileCode };
  return { label: "File", color: "#64748b", Icon: FileText };
}

function Loading() {
  return (
    <div className="flex items-center justify-center h-full" style={{ background: "#1a1d23" }}>
      <div className="flex flex-col items-center gap-3">
        <div className="relative w-8 h-8">
          <div className="absolute inset-0 rounded-full border-2 border-transparent border-t-blue-400 animate-spin" style={{ opacity: 0.6 }} />
          <div className="absolute inset-1 rounded-full border border-blue-400/20 animate-pulse" />
        </div>
        <span className="text-xs" style={{ color: "rgba(255,255,255,0.2)" }}>Loading preview…</span>
      </div>
    </div>
  );
}

function renderContent(preview: FilePreview) {
  const ext = getFileExt(preview.file_name);
  const resolvedDownloadUrl = preview.download_url ? resolveMediaUrl(preview.download_url) : "";
  const previewUrl = resolvedDownloadUrl
    ? resolvedDownloadUrl + (resolvedDownloadUrl.includes("?") ? "&" : "?") + "inline=true"
    : "";

  // Images — centered on dark background with subtle border
  if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext)) {
    return (
      <div className="flex items-center justify-center h-full p-6" style={{ background: "#0f1117" }}>
        <div className="relative max-w-full max-h-full">
          <img
            src={previewUrl || resolvedDownloadUrl}
            alt={preview.file_name}
            className="max-w-full max-h-full object-contain rounded-lg"
            style={{ boxShadow: "0 0 60px rgba(59,130,246,0.08), 0 4px 24px rgba(0,0,0,0.4)" }}
          />
        </div>
      </div>
    );
  }

  // PDF
  if (ext === "pdf" && previewUrl) {
    return (
      <Suspense fallback={<Loading />}>
        <PdfPreview url={previewUrl} />
      </Suspense>
    );
  }

  // DOCX
  if (["docx", "doc"].includes(ext) && previewUrl) {
    return (
      <Suspense fallback={<Loading />}>
        <DocxPreview url={previewUrl} />
      </Suspense>
    );
  }

  // Excel
  if (["xlsx", "xls"].includes(ext) && previewUrl) {
    return (
      <Suspense fallback={<Loading />}>
        <ExcelPreview url={previewUrl} />
      </Suspense>
    );
  }

  // Table data from preview API
  if (preview.preview_type === "table" || preview.preview_type === "excel") {
    const cols = preview.columns || [];
    const rws = preview.rows || [];
    if (cols.length > 0 && rws.length > 0) {
      return <CanvasTableRenderer columns={cols} rows={rws} />;
    }
  }

  // Text / Code — use Canvas viewer for all text-based content
  const textExts = ["txt", "md", "py", "js", "ts", "jsx", "tsx", "json", "yaml", "yml", "log", "html", "css", "xml", "csv", "tsv", "sh", "ini", "cfg", "toml"];
  if (textExts.includes(ext) && preview.content) {
    return <CanvasTextViewer content={preview.content} />;
  }
  if (preview.content) {
    return <CanvasTextViewer content={preview.content} />;
  }

  // Unsupported
  return (
    <div className="flex flex-col items-center justify-center h-full gap-5" style={{ background: "#1a1d23" }}>
      <div
        className="w-16 h-16 rounded-2xl flex items-center justify-center"
        style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.05)" }}
      >
        <FileType size={28} strokeWidth={1.2} style={{ color: "rgba(255,255,255,0.15)" }} />
      </div>
      <div className="text-sm" style={{ color: "rgba(255,255,255,0.25)" }}>
        暂不支持预览此文件格式
      </div>
      {resolvedDownloadUrl && (
        <a
          href={resolvedDownloadUrl}
          download={preview.file_name}
          className="px-4 py-2 rounded-lg text-xs font-medium transition-all duration-200 hover:scale-105"
          style={{
            background: "rgba(59,130,246,0.1)",
            color: "rgba(147,197,253,0.9)",
            border: "1px solid rgba(59,130,246,0.15)",
          }}
        >
          Download file
        </a>
      )}
    </div>
  );
}

export function FilePreviewPanel({ preview, onClose }: FilePreviewPanelProps) {
  const ext = getFileExt(preview.file_name);
  const cat = extCategory(ext);
  const Icon = cat.Icon;

  return (
    <div
      className="flex flex-col h-full animate-slide-in-right relative"
      style={{ background: "#13151a" }}
    >
      {/* Ambient top glow */}
      <div
        className="absolute top-0 left-0 right-0 h-px pointer-events-none"
        style={{ background: `linear-gradient(90deg, transparent 0%, ${cat.color}40 20%, ${cat.color}20 50%, transparent 100%)` }}
      />

      {/* Header — glass surface */}
      <div
        className="shrink-0 relative overflow-hidden"
        style={{
          background: "linear-gradient(180deg, rgba(255,255,255,0.03) 0%, rgba(255,255,255,0.01) 100%)",
          borderBottom: "1px solid rgba(255,255,255,0.04)",
        }}
      >
        {/* Top accent bar */}
        <div className="h-0.5" style={{ background: cat.color, opacity: 0.4 }} />

        <div className="flex items-center gap-4 px-5 py-4">
          {/* Icon */}
          <div
            className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
            style={{
              background: `${cat.color}12`,
              border: `1px solid ${cat.color}20`,
            }}
          >
            <Icon size={20} strokeWidth={1.6} style={{ color: cat.color }} />
          </div>

          {/* File info */}
          <div className="flex-1 min-w-0">
            <div
              className="text-sm font-semibold truncate"
              style={{ color: "rgba(255,255,255,0.85)", letterSpacing: "-0.01em" }}
              title={preview.file_name}
            >
              {preview.file_name}
            </div>
            <div className="flex items-center gap-2 mt-0.5">
              <span
                className="text-[10px] font-medium uppercase tracking-wider px-1.5 py-0.5 rounded"
                style={{ background: `${cat.color}18`, color: cat.color }}
              >
                {cat.label}
              </span>
              <span className="text-[11px]" style={{ color: "rgba(255,255,255,0.2)" }}>
                {formatFileSize(preview.size)}
              </span>
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-1">
            {preview.download_url && (
              <a href={resolveMediaUrl(preview.download_url)} download={preview.file_name}>
                <button
                  className="w-8 h-8 rounded-lg flex items-center justify-center transition-all duration-150 hover:scale-105"
                  style={{ color: "rgba(255,255,255,0.3)" }}
                  title="Download"
                >
                  <Download size={15} strokeWidth={1.8} />
                </button>
              </a>
            )}
            <button
              onClick={onClose}
              className="w-8 h-8 rounded-lg flex items-center justify-center transition-all duration-150 hover:scale-105"
              style={{ color: "rgba(255,255,255,0.3)" }}
              title="Close"
            >
              <X size={16} strokeWidth={1.8} />
            </button>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-hidden">{renderContent(preview)}</div>
    </div>
  );
}
