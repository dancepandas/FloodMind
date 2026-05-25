import type { FilePreview, UploadedFileItem, WorkflowPlan } from "@/types/app";
interface ContextPanelProps {
    sessionId: string;
    files: UploadedFileItem[];
    workflow?: WorkflowPlan | null;
    selectedPreview?: FilePreview | null;
    onPreviewFile: (fileId: string) => void;
    onClosePreview: () => void;
}
export declare function ContextPanel({ sessionId, files, workflow, selectedPreview, onPreviewFile, onClosePreview }: ContextPanelProps): import("react/jsx-runtime").JSX.Element;
export {};
