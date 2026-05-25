import type { GeneratedArtifact } from "@/types/app";
interface DocumentPreviewDialogProps {
    artifact: GeneratedArtifact;
    open: boolean;
    onOpenChange: (open: boolean) => void;
}
declare function getFileExt(filename: string): string;
declare function isPreviewable(filename: string): boolean;
declare function getFileIcon(filename: string): import("react/jsx-runtime").JSX.Element;
declare function getPreviewUrl(downloadUrl: string | undefined): string;
export declare function DocumentPreviewDialog({ artifact, open, onOpenChange }: DocumentPreviewDialogProps): import("react/jsx-runtime").JSX.Element;
export { isPreviewable, getFileExt, getFileIcon, getPreviewUrl };
