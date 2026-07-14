import { lazy, useMemo, Suspense } from "react";
import { Download, FileSpreadsheet, FileText, FileType } from "lucide-react";
import { Dialog, DialogContent, DialogTitle, DialogDescription, DialogHeader, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import type { GeneratedArtifact } from "@/types/app";
import { resolveMediaUrl } from "@/api/client";

const PdfPreview = lazy(() => import("./previews/PdfPreview").then((m) => ({ default: m.PdfPreview })));
const DocxPreview = lazy(() => import("./previews/DocxPreview").then((m) => ({ default: m.DocxPreview })));
const ExcelPreview = lazy(() => import("./previews/ExcelPreview").then((m) => ({ default: m.ExcelPreview })));

interface DocumentPreviewDialogProps {
  artifact: GeneratedArtifact;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function getFileExt(filename: string): string {
  return filename.split(".").pop()?.toLowerCase() || "";
}

function isPreviewable(filename: string): boolean {
  const ext = getFileExt(filename);
  return ["pdf", "docx", "xlsx", "xls"].includes(ext);
}

function getFileIcon(filename: string) {
  const ext = getFileExt(filename);
  if (ext === "pdf") return <FileType size={20} className="text-red-500" strokeWidth={1.8} />;
  if (["docx", "doc"].includes(ext)) return <FileText size={20} className="text-blue-500" strokeWidth={1.8} />;
  if (["xlsx", "xls"].includes(ext)) return <FileSpreadsheet size={20} className="text-green-600" strokeWidth={1.8} />;
  return <FileText size={20} className="text-muted-foreground" strokeWidth={1.8} />;
}

function getPreviewUrl(downloadUrl: string | undefined): string {
  if (!downloadUrl) return "";
  const resolved = resolveMediaUrl(downloadUrl);
  const sep = resolved.includes("?") ? "&" : "?";
  return `${resolved}${sep}inline=true`;
}

function getDialogClass(ext: string): string {
  if (["xlsx", "xls"].includes(ext)) {
    return "sm:max-w-6xl h-[85vh]";
  }
  return "sm:max-w-5xl h-[85vh]";
}

function PreviewLoader() {
  return (
    <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
      <div className="flex flex-col items-center gap-2">
        <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        <span>正在加载预览器...</span>
      </div>
    </div>
  );
}

export function DocumentPreviewDialog({ artifact, open, onOpenChange }: DocumentPreviewDialogProps) {
  const ext = getFileExt(artifact.filename);
  const previewUrl = getPreviewUrl(artifact.download_url);
  const dialogClass = useMemo(() => getDialogClass(ext), [ext]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={`${dialogClass} flex flex-col p-0 gap-0 overflow-hidden`}>
        <DialogHeader className="px-4 py-3 border-b border-border flex-shrink-0">
          <div className="flex items-center gap-2">
            {getFileIcon(artifact.filename)}
            <DialogTitle className="text-sm font-medium truncate">{artifact.filename}</DialogTitle>
          </div>
          <DialogDescription className="sr-only">文档预览</DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-hidden">
          <Suspense fallback={<PreviewLoader />}>
            {ext === "pdf" && <PdfPreview url={previewUrl} />}
            {ext === "docx" && <DocxPreview url={previewUrl} />}
            {["xlsx", "xls"].includes(ext) && <ExcelPreview url={previewUrl} />}
          </Suspense>
        </div>

        <DialogFooter className="px-4 py-3 border-t border-border flex-shrink-0">
          {artifact.download_url && (
            <a href={resolveMediaUrl(artifact.download_url)} download={artifact.filename}>
              <Button variant="outline" size="sm">
                <Download size={14} className="mr-1.5" />
                下载文件
              </Button>
            </a>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export { isPreviewable, getFileExt, getFileIcon, getPreviewUrl };
