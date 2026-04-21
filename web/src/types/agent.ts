// Types for Chat Messages
export type BlockType = 'text' | 'thought';

export interface ContentBlock {
  type: BlockType;
  content: string;
  isComplete?: boolean; // For thinking blocks to show loading vs done
}

export interface Message {
  id: string;
  role: 'user' | 'agent';
  blocks: ContentBlock[];
  timestamp: Date;
}

// Types for History Sessions
export interface ChatSession {
  id: string;
  title: string;
  date: string;
  isActive?: boolean;
}

// Types for Right Panel Context
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