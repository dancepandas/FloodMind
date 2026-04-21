export type MessageBlockType = "thought" | "answer";

export interface MessageBlock {
  id: string;
  type: MessageBlockType;
  content: string;
  isCollapsed?: boolean;
  isStreaming?: boolean;
  isArchived?: boolean;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  reasoning?: string;
  rawReasoning?: string;
  blocks: MessageBlock[];
  timestamp: string;
  artifacts?: GeneratedArtifact[];
}

export interface SessionSummary {
  session_id: string;
  title?: string;
  updated_at?: string;
  created_at?: string;
}

export interface UploadedFileItem {
  id: string;
  name: string;
  path?: string;
  size: number;
  upload_time?: string;
}

export interface FilePreviewSheet {
  sheet_name: string;
  columns: string[];
  rows: string[][];
}

export interface FilePreview {
  file_id: string;
  file_name: string;
  size: number;
  preview_type: "unsupported" | "missing" | "text" | "table" | "excel";
  content?: string;
  columns?: string[];
  rows?: string[][];
  sheets?: FilePreviewSheet[];
}

export interface GeneratedArtifact {
  type: "file_generated" | "image_generated";
  filename: string;
  filepath: string;
  size?: number;
  download_url?: string;
  image_url?: string;
  image_data?: string;
}

export interface ReferenceLink {
  title: string;
  url?: string;
  source?: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  reasoning?: string;
  rawReasoning?: string;
  blocks: MessageBlock[];
  timestamp: string;
  artifacts?: GeneratedArtifact[];
  references?: ReferenceLink[];
}

export interface ToolActivity {
  id: string;
  toolName: string;
  status: "running" | "done" | "error";
  content: string;
  timestamp: string;
}

export interface WorkflowStepItem {
  key: string;
  label: string;
  title: string;
  status: "pending" | "running" | "completed" | "error";
  detail?: string;
  outcome?: string;
  expected_deliverables?: { type: string; format?: string; description?: string }[];
  output_artifacts?: string[];
}

export interface WorkflowPlan {
  title: string;
  steps: WorkflowStepItem[];
}

export interface SessionConfig {
  enable_search: boolean;
  enable_rag: boolean;
  enable_reasoning: boolean;
}

export interface SessionRuntimeState {
  isPaused: boolean;
}

export interface StreamSnapshot {
  message_id: string;
  content?: string;
  reasoning?: string;
  raw_reasoning?: string;
  is_streaming?: boolean;
  artifacts?: GeneratedArtifact[];
  workflow?: WorkflowPlan;
}
