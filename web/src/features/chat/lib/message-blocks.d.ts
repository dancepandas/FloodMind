import type { ChatMessage, GeneratedArtifact, ActionDetail } from "@/types/app";
export declare function getToolDisplayName(toolName: string): string;
export declare function createUserMessage(content: string): ChatMessage;
export declare function createAssistantMessage(id?: string): ChatMessage;
export declare function createSystemMessage(content: string): ChatMessage;
export declare function appendThoughtBlock(message: ChatMessage, content: string, append?: boolean): ChatMessage;
export declare function appendAnswerBlock(message: ChatMessage, content: string, append?: boolean): ChatMessage;
export declare function appendActionBlock(message: ChatMessage, toolName: string, status: ActionDetail["status"], content: string, delegation?: ActionDetail["delegation"], callId?: string, askId?: string, askReason?: string, askSessionId?: string): ChatMessage;
export declare function finalizeThoughtBlocks(message: ChatMessage): ChatMessage;
export declare function setAssistantFinalContent(message: ChatMessage, content: string): ChatMessage;
export declare function attachArtifact(message: ChatMessage, artifact: GeneratedArtifact): ChatMessage;
export declare function fromServerMessage(raw: Record<string, unknown>): ChatMessage;
export declare function updateActionBlockStatus(message: ChatMessage, callId: string, status: ActionDetail["status"], content: string, extra?: {
    askId?: string;
    askReason?: string;
    sessionId?: string;
}): ChatMessage;
