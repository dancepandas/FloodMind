import type { ScheduledTask } from "@/types/app";
interface ScheduledTaskResultDialogProps {
    task: ScheduledTask | null;
    open: boolean;
    onOpenChange: (open: boolean) => void;
}
export declare function ScheduledTaskResultDialog({ task, open, onOpenChange }: ScheduledTaskResultDialogProps): import("react/jsx-runtime").JSX.Element;
export {};
