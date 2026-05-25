import type { ChatMessage as ChatMessageModel, ActionDetail } from "@/types/app";
interface ChatMessageProps {
    message: ChatMessageModel;
    onToggleThought: (messageId: string, blockId: string) => void;
    onUpdateAction?: (callId: string, status: ActionDetail["status"], content: string) => void;
}
export declare function ChatMessage({ message, onToggleThought, onUpdateAction }: ChatMessageProps): import("react/jsx-runtime").JSX.Element;
export {};
