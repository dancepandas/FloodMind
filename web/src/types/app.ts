export type MessageBlockType = "thought" | "action" | "answer" | "error";

export interface ActionDetail {
  callId: string;
  toolName: string;
  status: "running" | "done" | "error" | "pending_confirmation";
  content: string;
  step_key?: string;
  askId?: string;
  askReason?: string;
  sessionId?: string;
  toolInput?: Record<string, unknown>;
  delegation?: {
    task: string;
    skill_name?: string;
    label: string;
    summary?: string;
  };
}

export interface MessageBlock {
  id: string;
  type: MessageBlockType;
  content: string;
  isCollapsed?: boolean;
  isStreaming?: boolean;
  isArchived?: boolean;
  actions?: ActionDetail[];
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
  preview_type: "unsupported" | "missing" | "text" | "table" | "excel" | "document";
  content?: string;
  columns?: string[];
  rows?: string[][];
  sheets?: FilePreviewSheet[];
  download_url?: string;
}

export interface GeneratedArtifact {
  type: "file_generated" | "image_generated";
  filename: string;
  filepath?: string;
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

export interface TokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface ChatMessage {
  id: string;
  role: "human" | "FloodMind" | "system";
  content: string;
  isComplete?: boolean;
  blocks: MessageBlock[];
  timestamp: string;
  artifacts?: GeneratedArtifact[];
  references?: ReferenceLink[];
  tokenUsage?: TokenUsage;
  attachments?: UploadedFileItem[];
}

export interface ToolActivity {
  id: string;
  toolName: string;
  status: "running" | "done" | "error" | "pending_confirmation";
  content: string;
  timestamp: string;
  askId?: string;
  askReason?: string;
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
  subtasks?: PlanStepSubtask[];
}

export interface PlanStepSubtask {
  id: string;
  content: string;
  status: "pending" | "in_progress" | "completed" | "cancelled";
  priority: "high" | "normal" | "low";
}

export interface WorkflowPlan {
  title: string;
  steps: WorkflowStepItem[];
}

export interface SessionConfig {
  model_key: string;
  enable_search: boolean;
  enable_reasoning: boolean;
}

export interface ModelOption {
  key: string;
  label: string;
  description: string;
  supports_reasoning: boolean;
  supports_search: boolean;
  supports_vision: boolean;
  supports_tool_calling: boolean;
  supports_tool_calling_with_vision?: boolean;
  supports_reasoning_with_vision?: boolean;
  max_image_count?: number;
  max_image_size_mb?: number;
  is_default: boolean;
}

export interface ModelsResponse {
  status: string;
  default_model_key: string;
  models: ModelOption[];
}

export interface PendingPermissionAsk {
  askId: string;
  callId: string;
  toolName: string;
  askReason: string;
  sessionId: string;
  toolInput?: Record<string, unknown>;
}

export interface SessionRuntimeState {
  isPaused: boolean;
}

export interface StreamSnapshot {
  message_id: string;
  content?: string;
  reasoning?: string;
  is_streaming?: boolean;
  artifacts?: GeneratedArtifact[];
  workflow?: WorkflowPlan;
}

export interface ScheduledTaskArtifact {
  filename: string;
  download_url: string;
  size?: number;
  created_at?: string;
}

export interface ScheduledTask {
  id: string;
  session_id: string;
  command: string;
  repeat: "none" | "daily" | string;
  enabled: boolean;
  run_time?: string;
  scheduled_at?: string;
  next_run_at?: string;
  status: "pending" | "running" | "completed" | "failed" | "disabled" | string;
  last_status?: string;
  last_run_at?: string;
  last_finished_at?: string;
  last_result?: string;
  last_error?: string;
  artifacts?: ScheduledTaskArtifact[];
  attempt_count?: number;
  created_at?: string;
  updated_at?: string;
}

export interface CheckpointSummary {
  checkpoint_id: string;
  status: string;
  iteration: number;
  created_at: string;
  has_files_snapshot: boolean;
}

export interface CheckpointManifest {
  checkpoint_id: string;
  session_id: string;
  run_id: string;
  parent_checkpoint_id?: string;
  status: string;
  iteration: number;
  created_at: string;
  state_file: string;
  files_snapshot_dir?: string;
  files_snapshot_base_dirs: string[];
  metadata: Record<string, unknown>;
}

export interface TraceSpan {
  span_id: string;
  parent_id?: string;
  trace_id: string;
  type: string;
  name: string;
  start_time: string;
  end_time?: string;
  duration_ms?: number;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  status: string;
  metadata: Record<string, unknown>;
}

export interface TraceEvent {
  event_id: string;
  trace_id: string;
  type: string;
  name: string;
  timestamp: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  status: string;
  metadata: Record<string, unknown>;
}

export type TraceItem = TraceSpan | TraceEvent;

