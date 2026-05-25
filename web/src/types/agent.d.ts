export type BlockType = 'text' | 'thought';
export interface ContentBlock {
    type: BlockType;
    content: string;
    isComplete?: boolean;
}
export interface Message {
    id: string;
    role: 'user' | 'agent';
    blocks: ContentBlock[];
    timestamp: Date;
}
export interface ChatSession {
    id: string;
    title: string;
    date: string;
    isActive?: boolean;
}
export interface UploadedFile {
    id: string;
    name: string;
    size: string;
    type: 'pdf' | 'doc' | 'image' | 'csv';
}
export interface ToolExecution {
    id: string;
    toolName: string;
    status: 'running' | 'success' | 'error';
    result?: string;
}
export interface PlanStep {
    id: string;
    description: string;
    status: 'pending' | 'in_progress' | 'completed';
}
