import type { ChatMessage as ChatMessageModel, ModelOption, SessionConfig, ActionDetail, PendingPermissionAsk } from "@/types/app";
interface ChatAreaProps {
    messages: ChatMessageModel[];
    inputValue: string;
    isStreaming: boolean;
    isPaused: boolean;
    availableModels: ModelOption[];
    config: SessionConfig;
    onInputChange: (value: string) => void;
    onSubmit: () => void;
    onPause: () => void;
    onUpload: (file: File) => void;
    onToggleThought: (messageId: string, blockId: string) => void;
    onUpdateAction?: (callId: string, status: ActionDetail["status"], content: string) => void;
    onConfigChange: (config: SessionConfig) => void;
    pendingPermissionAsk: PendingPermissionAsk | null;
    onRespondPermissionAsk: (approved: boolean) => void;
}
export declare function ChatArea({ messages, inputValue, isStreaming, isPaused, availableModels, config, onInputChange, onSubmit, onPause, onUpload, onToggleThought, onUpdateAction, onConfigChange, pendingPermissionAsk, onRespondPermissionAsk, }: ChatAreaProps): import("react/jsx-runtime").JSX.Element;
export {};
