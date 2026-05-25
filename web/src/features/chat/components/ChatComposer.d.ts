import type { ModelOption, SessionConfig, PendingPermissionAsk } from "@/types/app";
interface ChatComposerProps {
    value: string;
    disabled?: boolean;
    isRunning?: boolean;
    models: ModelOption[];
    config: SessionConfig;
    onChange: (value: string) => void;
    onSubmit: () => void;
    onPause: () => void;
    onUpload: (file: File) => void;
    onConfigChange: (config: SessionConfig) => void;
    pendingPermissionAsk: PendingPermissionAsk | null;
    onRespondPermissionAsk: (approved: boolean) => void;
}
export declare function ChatComposer({ value, disabled, isRunning, models, config, onChange, onSubmit, onPause, onUpload, onConfigChange, pendingPermissionAsk, onRespondPermissionAsk, }: ChatComposerProps): import("react/jsx-runtime").JSX.Element;
export {};
