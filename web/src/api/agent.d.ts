import type { FilePreview, SessionConfig, SessionSummary, ScheduledTask, StreamSnapshot, UploadedFileItem, ModelsResponse } from "@/types/app";
interface SessionDetailResponse {
    status: string;
    session: SessionSummary;
    messages: Array<Record<string, unknown>>;
    artifacts: Array<Record<string, unknown>>;
    in_progress?: StreamSnapshot | null;
}
interface InitResponse {
    status: string;
    model_key: string;
    model_name: string;
    enable_search: boolean;
    enable_reasoning: boolean;
}
interface ConfigResponse {
    status: string;
    config: SessionConfig & {
        model_name?: string;
    };
}
interface SessionStatusResponse {
    status: string;
    in_progress?: StreamSnapshot | null;
    session_state?: Record<string, unknown>;
}
export declare function initAgent(sessionId: string, config: SessionConfig): Promise<InitResponse>;
export declare function updateSessionConfig(sessionId: string, config: Partial<SessionConfig>): Promise<ConfigResponse>;
export declare function fetchModels(): Promise<ModelsResponse>;
export declare function fetchSessions(): Promise<SessionSummary[]>;
export declare function fetchSession(sessionId: string): Promise<SessionDetailResponse>;
export declare function fetchSessionFiles(sessionId: string): Promise<UploadedFileItem[]>;
export declare function fetchFilePreview(sessionId: string, fileId: string): Promise<FilePreview>;
export declare function uploadFile(sessionId: string, file: File): Promise<void>;
export declare function fetchSessionStatus(sessionId: string): Promise<SessionStatusResponse>;
export declare function pauseSession(sessionId: string): Promise<void>;
export declare function resumeSession(sessionId: string): Promise<void>;
export declare function saveSession(sessionId: string): Promise<void>;
export declare function deleteSession(sessionId: string): Promise<void>;
export declare function fetchScheduledTasks(sessionId?: string): Promise<ScheduledTask[]>;
export declare function deleteScheduledTask(taskId: string): Promise<void>;
export declare function createChatRequest(sessionId: string, message: string, uploadedFiles: string[], assistantMessageId: string): Promise<Response>;
export declare function resumeStreamRequest(sessionId: string, afterIndex?: number): Promise<Response>;
export declare function respondPermissionAsk(askId: string, approved: boolean, sessionId: string): Promise<{
    status: string;
    ask_id?: string;
    approved?: boolean;
    message?: string;
}>;
export declare function downloadLogsZip(): void;
export declare function downloadSessionOutputs(sessionId: string): void;
export {};
