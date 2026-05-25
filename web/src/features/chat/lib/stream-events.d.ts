import type { ChatMessage, ToolActivity, WorkflowPlan } from "@/types/app";
interface StreamHandlers {
    updateAssistant: (updater: (message: ChatMessage) => ChatMessage) => void;
    pushToolActivity: (toolName: string, content: string, status: ToolActivity["status"]) => void;
    setWorkflow: (updater: WorkflowPlan | ((prev: WorkflowPlan | null) => WorkflowPlan | null)) => void;
}
export declare function applyStreamEvent(data: Record<string, any>, handlers: StreamHandlers): void;
export {};
