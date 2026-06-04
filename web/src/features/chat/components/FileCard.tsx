import { FileText, FileSpreadsheet, FileType, FileImage, File, X } from "lucide-react";
import type { UploadedFileItem } from "@/types/app";

function getFileExt(filename: string): string {
  return filename.split(".").pop()?.toLowerCase() || "";
}

interface FileCardConfig {
  icon: typeof File;
  color: string;
  bg: string;
  label: string;
}

const FILE_TYPE_CONFIGS: Record<string, FileCardConfig> = {
  pdf:  { icon: FileType,         color: "#ef4444", bg: "#fef2f2", label: "PDF" },
  docx: { icon: FileText,         color: "#3b82f6", bg: "#eff6ff", label: "Word" },
  doc:  { icon: FileText,         color: "#3b82f6", bg: "#eff6ff", label: "Word" },
  xlsx: { icon: FileSpreadsheet,  color: "#16a34a", bg: "#f0fdf4", label: "Excel" },
  xls:  { icon: FileSpreadsheet,  color: "#16a34a", bg: "#f0fdf4", label: "Excel" },
  csv:  { icon: FileSpreadsheet,  color: "#16a34a", bg: "#f0fdf4", label: "CSV" },
  png:  { icon: FileImage,        color: "#a855f7", bg: "#faf5ff", label: "Image" },
  jpg:  { icon: FileImage,        color: "#a855f7", bg: "#faf5ff", label: "Image" },
  jpeg: { icon: FileImage,        color: "#a855f7", bg: "#faf5ff", label: "Image" },
  gif:  { icon: FileImage,        color: "#a855f7", bg: "#faf5ff", label: "Image" },
  webp: { icon: FileImage,        color: "#a855f7", bg: "#faf5ff", label: "Image" },
  bmp:  { icon: FileImage,        color: "#a855f7", bg: "#faf5ff", label: "Image" },
  svg:  { icon: FileImage,        color: "#a855f7", bg: "#faf5ff", label: "SVG" },
  txt:  { icon: FileText,         color: "#64748b", bg: "#f8fafc", label: "Text" },
  md:   { icon: FileText,         color: "#64748b", bg: "#f8fafc", label: "MD" },
  json: { icon: FileText,         color: "#64748b", bg: "#f8fafc", label: "JSON" },
};

function getFileConfig(filename: string): FileCardConfig {
  const ext = getFileExt(filename);
  return FILE_TYPE_CONFIGS[ext] || { icon: File, color: "#64748b", bg: "#f8fafc", label: ext.toUpperCase() || "FILE" };
}

export function formatFileSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const k = 1024;
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

interface FileCardProps {
  file: UploadedFileItem;
  onClick?: () => void;
  onRemove?: () => void;
}

export function FileCard({ file, onClick, onRemove }: FileCardProps) {
  const config = getFileConfig(file.name);
  const Icon = config.icon;

  return (
    <button
      type="button"
      onClick={onClick}
      className="group relative flex flex-col items-center gap-1 flex-shrink-0 w-[72px] transition-all duration-200"
    >
      {/* Icon container */}
      <div
        className="relative w-12 h-12 rounded-xl flex items-center justify-center transition-all duration-200"
        style={{
          background: config.bg,
          boxShadow: `0 1px 3px rgba(0,0,0,0.04), inset 0 1px 0 rgba(255,255,255,0.6)`,
        }}
      >
        <Icon size={22} strokeWidth={1.6} style={{ color: config.color }} />
        {/* Type badge */}
        <span
          className="absolute -bottom-1 right-0 text-[7px] font-bold px-1 py-px rounded-md"
          style={{
            background: config.color,
            color: '#fff',
            lineHeight: 1.2,
          }}
        >
          {config.label}
        </span>

        {/* Remove button */}
        {onRemove && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onRemove();
            }}
            className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-all duration-150"
            style={{
              background: 'hsl(var(--background))',
              border: '1px solid hsl(var(--border))',
              color: 'hsl(var(--muted-foreground))',
              boxShadow: '0 1px 3px rgba(0,0,0,0.08)',
            }}
          >
            <X size={9} strokeWidth={2.5} />
          </button>
        )}
      </div>

      {/* File name */}
      <span
        className="text-[10px] font-medium leading-tight text-center w-full truncate px-0.5 transition-colors duration-200"
        style={{ color: 'hsl(var(--foreground))' }}
      >
        {file.name}
      </span>

      {/* File size */}
      <span
        className="text-[9px] font-mono leading-none"
        style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.4 }}
      >
        {formatFileSize(file.size)}
      </span>
    </button>
  );
}