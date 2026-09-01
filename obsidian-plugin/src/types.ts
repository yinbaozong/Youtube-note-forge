export type PipelineStatus = "idle" | "running" | "ok" | "error" | "cancelled";

export type PipelineStage =
  | "credentials"
  | "materials"
  | "planning"
  | "frames"
  | "writing"
  | "validation"
  | "complete"
  | "failed"
  | "cancelled"
  | "idle";

export interface PipelineResult {
  status?: string;
  stage?: string;
  code?: string;
  message?: string;
  action?: string;
  note?: string;
  transcript?: string;
  cover?: string | null;
  manifest?: string;
  screenshots?: unknown[];
  errors?: Array<{ code?: string; message?: string }>;
  [key: string]: unknown;
}

export interface PublicFailure {
  status: "error";
  code: string;
  message: string;
  can_retry_asr: boolean;
  auto_retry: false;
  stage?: string;
  technical_message?: string;
}
