export type ProviderType = "openrouter" | "ollama" | "openai_compatible";

export interface ModelInfo {
  id: string;
  name?: string;
  context_length?: number;
  prompt_price?: number | null;
  completion_price?: number | null;
  supports_tools?: boolean;
}

export interface Provider {
  id: string;
  name: string;
  type: ProviderType;
  base_url: string;
  api_key_masked: string | null;
  has_api_key: boolean;
  default_model: string;
  available_models: ModelInfo[];
  supports_tools: boolean | null;
  is_default: boolean;
  params: Record<string, number>;
  effective_params: { temperature: number; max_tokens: number; timeout_s: number; retries: number };
  extra_headers: { http_referer?: string; x_title?: string };
  created_at: string;
}

export interface ProviderDefaults {
  [k: string]: any;
}

export type SourceStatus =
  | "uploaded"
  | "profiled"
  | "draft_ready"
  | "in_review"
  | "onboarded"
  | "failed"
  | "archived";

export interface ParseOptions {
  delimiter: string | null;
  header: boolean | null;
  encoding: "utf-8" | "latin-1" | null;
  quote: string | null;
  skip_rows: number | null;
}

export interface Source {
  id: string;
  name: string;
  description: string;
  type: string;
  original_filename: string;
  row_count: number | null;
  column_count: number | null;
  size_bytes: number;
  status: SourceStatus;
  error: string | null;
  parse_options: ParseOptions;
  ingest_report: { malformed_rows?: number; malformed_sample?: any[] };
  active_semantic_version_id: string | null;
  active_version: number | null;
  draft_version_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface Preview {
  options: ParseOptions;
  columns: { original: string; name: string; type: string }[];
  rows: any[][];
  warnings: string[];
  malformed_rows: number;
  malformed_sample: { line: number; error: string; raw: string; message: string }[];
}

export interface ProfileColumn {
  name: string;
  original_name: string;
  storage_type: string;
  inferred_type: string;
  null_pct: number;
  distinct_count: number;
  distinct_is_estimate: boolean;
  min: any;
  max: any;
  mean?: number | null;
  median?: number | null;
  std?: number | null;
  top_values: { value: any; count: number }[];
  sample_values: any[];
  length?: { min: number; max: number; avg: number };
  pattern_hints: string[];
  pii: boolean;
  candidate_key?: boolean;
}

export interface Profile {
  row_count: number;
  sampled: boolean;
  sample_rows: number;
  columns: ProfileColumn[];
  candidate_keys: string[];
  profile_ms: number;
  generated_at: string;
}

export interface VersionSummary {
  id: string;
  version: number;
  status: "draft" | "approved" | "superseded";
  origin: string;
  generated_by_provider: string | null;
  generated_by_model: string | null;
  prompt_version: string | null;
  approved_at: string | null;
  change_notes: string;
  created_at: string;
  updated_at: string;
}

export interface SourceDetail {
  source: Source;
  profile: Profile | null;
  column_map: Record<string, string>;
  versions: VersionSummary[];
}

export type Role = "dimension" | "measure" | "time_dimension" | "identifier" | "ignore";
export type DataType = "string" | "integer" | "float" | "boolean" | "date" | "datetime";
export type Aggregation = "sum" | "avg" | "min" | "max" | "count" | "count_distinct";
export type Confidence = "high" | "medium" | "low";

export interface SemColumn {
  name: string;
  display_name: string;
  description: string;
  data_type: DataType;
  role: Role;
  default_aggregation: Aggregation | null;
  unit: string | null;
  format: string | null;
  synonyms: string[];
  allowed_values: string[] | null;
  pii: boolean;
  confidence: Confidence | null;
  rationale: string | null;
  provenance: Record<string, string>;
  review_status: "pending" | "accepted";
}

export interface SemMetric {
  name: string;
  display_name: string;
  description: string;
  expression: string;
  filters: string[];
  unit: string | null;
  synonyms: string[];
  confidence: Confidence | null;
  rationale: string | null;
  provenance: Record<string, string>;
  review_status: "pending" | "accepted";
}

export interface SemModel {
  entity: {
    name: string;
    display_name: string;
    description: string;
    grain: string;
    confidence?: Confidence | null;
    rationale?: string | null;
    provenance?: Record<string, string>;
  };
  columns: SemColumn[];
  metrics: SemMetric[];
  relationships: any[];
  sample_questions: string[];
}

export interface Issue {
  path: string;
  message: string;
  severity: "error" | "warning";
}

export interface VersionDetail extends VersionSummary {
  source_id: string;
  model: SemModel;
  base: SemModel;
  issues: Issue[];
  is_active: boolean;
}

export interface Job {
  id: string;
  status: "pending" | "running" | "succeeded" | "failed" | "cancelled";
  progress: string[];
  error: string | null;
  result_version_id: string | null;
}

export interface RuntimeSettings {
  sample_values_per_column: number;
  strict_privacy: boolean;
  profile_sample_rows: number;
  query_timeout_s: number;
  row_cap: number;
  get_rows_max: number;
  raw_sql_enabled: boolean;
  aggregates_only: boolean;
  max_agent_iterations: number;
  agent_timeout_s: number;
  context_budget_tokens: number;
  debug_payloads: boolean;
  generation_provider_id: string | null;
  generation_model: string | null;
  chat_provider_id: string | null;
  chat_model: string | null;
}

export interface ChatSession {
  id: string;
  title: string;
  source_id: string;
  source_name: string | null;
  semantic_version_id: string;
  semantic_version: number | null;
  newer_version_available: boolean;
  provider_id: string | null;
  provider_name: string | null;
  supports_tools: boolean | null;
  model: string;
  is_test: boolean;
  created_at: string;
  updated_at: string;
}

export interface ToolResultUI {
  columns?: string[];
  rows?: any[][];
  total_rows?: number;
  truncated?: boolean;
  filters_applied?: string[];
  sql?: string;
  params?: any[];
  execution_ms?: number;
  cached?: boolean;
  tool_ms?: number;
  error?: string;
  column_meta?: {
    name: string;
    kind: "dimension" | "time" | "metric";
    display_name: string;
    unit?: string;
  }[];
  /** Set by analysis tools: which metrics to plot, and an optional [lower, upper] interval band. */
  chart?: { kind: "bar" | "line"; y: string[]; band?: [string, string] };
  [k: string]: any;
}

export interface ChatMessage {
  id: string;
  seq: number;
  role: "user" | "assistant" | "tool";
  content: string;
  tool_calls: { id: string; name: string; arguments: any }[];
  tool_results: { tool_call_id: string; name: string; ok: boolean; args: any; result: ToolResultUI }[];
  tokens_in: number | null;
  tokens_out: number | null;
  latency_ms: number | null;
  created_at: string;
}
