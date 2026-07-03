import { Eye, Download, ZoomIn, X, ExternalLink, FileText } from "lucide-react";
import type { GeneratedArtifact, ReferenceLink } from "@/types/app";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { isPreviewable, getFileIcon } from "../DocumentPreviewDialog";

/** 图片产物全屏预览弹窗。 */
export function ImagePreviewDialog({ artifact, open, onOpenChange }: { artifact: GeneratedArtifact; open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-4xl p-0 overflow-hidden" style={{ background: "rgba(0,0,0,0.95)" }} showCloseButton={false}>
        <DialogTitle className="sr-only">{artifact.filename}</DialogTitle>
        <DialogDescription className="sr-only">图片预览</DialogDescription>
        <div className="relative flex items-center justify-center min-h-[200px]">
          <img src={artifact.image_url || artifact.download_url} alt={artifact.filename} className="max-w-full max-h-[80vh] object-contain" />
          <div className="absolute top-3 right-3 flex gap-2">
            {artifact.download_url && (
              <a href={artifact.download_url} download={artifact.filename} className="rounded-full bg-white/10 backdrop-blur-sm p-1.5 text-white/80 hover:bg-white/20 transition-colors duration-150" title="下载图片">
                <Download size={16} />
              </a>
            )}
            <button type="button" onClick={() => onOpenChange(false)} className="rounded-full bg-white/10 backdrop-blur-sm p-1.5 text-white/80 hover:bg-white/20 transition-colors duration-150" title="关闭">
              <X size={16} />
            </button>
          </div>
          <div className="absolute bottom-3 left-3 text-white/45 text-[11px] bg-black/20 backdrop-blur-sm px-2 py-0.5 rounded-md">
            {artifact.filename}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** 产物卡片列表：图片缩略图可点开预览，文件可预览/下载。 */
export function ArtifactList({ artifacts, onPreview }: { artifacts: GeneratedArtifact[]; onPreview: (a: GeneratedArtifact) => void }) {
  return (
    <div className="mt-2.5 flex flex-col gap-2.5 items-start">
      {artifacts.map((artifact) =>
        artifact.type === "image_generated" ? (
          <div key={artifact.download_url || artifact.image_url || `${artifact.type}-${artifact.filename}`}
            className="w-[35%] min-w-[200px] overflow-hidden rounded-xl group"
            style={{ border: "1px solid var(--border)", background: "var(--surface)" }}
          >
            {artifact.image_url && (
              <div className="relative h-24 w-full overflow-hidden cursor-pointer" style={{ borderBottom: "1px solid var(--border)" }}
                onClick={() => onPreview(artifact)}
              >
                <img src={artifact.image_url} alt={artifact.filename} className="h-full w-full object-cover" />
                <div className="absolute inset-0 bg-black/0 group-hover:bg-black/12 transition-colors duration-200 flex items-center justify-center">
                  <ZoomIn size={20} className="text-white opacity-0 group-hover:opacity-70 transition-opacity duration-200" />
                </div>
              </div>
            )}
            <div className="px-2.5 py-2 flex items-center justify-between">
              <div className="font-medium truncate flex-1 text-[12px]" style={{ color: "var(--text-primary)" }}>{artifact.filename}</div>
              {artifact.download_url && (
                <a href={artifact.download_url} download={artifact.filename}
                  className="ml-1.5 flex-shrink-0 rounded p-1 transition-colors duration-150"
                  style={{ color: "var(--text-tertiary)" }}
                  onClick={(e) => e.stopPropagation()}
                >
                  <Download size={13} />
                </a>
              )}
            </div>
          </div>
        ) : (
          <div key={artifact.download_url || `${artifact.type}-${artifact.filename}`}
            className="w-[35%] min-w-[200px] rounded-xl overflow-hidden"
            style={{ border: "1px solid var(--border)", background: "var(--surface)" }}
          >
            <div className="px-2.5 py-2 flex items-center gap-2">
              {getFileIcon(artifact.filename)}
              <div className="font-medium truncate flex-1 text-[12px]" style={{ color: "var(--text-primary)" }}>{artifact.filename}</div>
            </div>
            <div className="px-2.5 pb-2 flex items-center gap-1">
              {isPreviewable(artifact.filename) && (
                <button type="button" onClick={() => onPreview(artifact)}
                  className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] transition-colors duration-150"
                  style={{ color: "var(--wave)" }}
                >
                  <Eye size={12} /> 预览
                </button>
              )}
              {artifact.download_url && (
                <a href={artifact.download_url} download={artifact.filename}
                  className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] transition-colors duration-150"
                  style={{ color: "var(--text-tertiary)" }}
                  onClick={(e) => e.stopPropagation()}
                >
                  <Download size={12} /> 下载
                </a>
              )}
            </div>
          </div>
        ),
      )}
    </div>
  );
}

export function ReferenceList({ references }: { references: ReferenceLink[] }) {
  return (
    <div className="mt-2.5 pt-2.5" style={{ borderTop: "1px solid var(--border)", opacity: 0.7 }}>
      <div className="text-[10px] font-semibold mb-1.5" style={{ color: "var(--text-secondary)" }}>参考来源</div>
      <div className="flex flex-col gap-1">
        {references.map((ref, i) => (
          <ReferenceItem key={i} reference={ref} index={i + 1} />
        ))}
      </div>
    </div>
  );
}

function ReferenceItem({ reference, index }: { reference: ReferenceLink; index: number }) {
  const isWeb = !!reference.url;
  const displayTitle = reference.title.length > 60 ? reference.title.slice(0, 60) + "…" : reference.title;

  if (isWeb) {
    return (
      <a href={reference.url} target="_blank" rel="noopener noreferrer"
        className="flex items-center gap-1.5 px-1.5 py-1 rounded-md text-[11px] transition-all duration-200"
        onMouseEnter={(e) => { e.currentTarget.style.background = "var(--surface-2)"; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
      >
        <span className="flex-shrink-0 w-4 h-4 rounded flex items-center justify-center text-[9px] font-bold" style={{ background: "var(--surface-2)", color: "var(--wave)" }}>
          {index}
        </span>
        <ExternalLink size={10} className="flex-shrink-0" style={{ color: "var(--text-tertiary)" }} />
        <span className="truncate" style={{ color: "var(--text-secondary)" }}>{displayTitle}</span>
        {reference.source && <span className="flex-shrink-0 text-[9px] ml-auto" style={{ color: "var(--text-tertiary)", opacity: 0.7 }}>{reference.source}</span>}
      </a>
    );
  }

  return (
    <div className="flex items-center gap-1.5 px-1.5 py-1 rounded-md text-[11px]">
      <span className="flex-shrink-0 w-4 h-4 rounded flex items-center justify-center text-[9px] font-bold" style={{ background: "var(--surface-2)", color: "var(--wave)" }}>
        {index}
      </span>
      <FileText size={10} className="flex-shrink-0" style={{ color: "var(--text-tertiary)" }} />
      <span className="truncate" style={{ color: "var(--text-secondary)" }}>{displayTitle}</span>
      {reference.source && <span className="flex-shrink-0 text-[9px] ml-auto" style={{ color: "var(--text-tertiary)", opacity: 0.7 }}>{reference.source}</span>}
    </div>
  );
}
