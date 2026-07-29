export type Status =
  | "completed"
  | "completed_with_failures"
  | "completed_with_revisit_failure"
  | "excluded"
  | "failed"
  | "queued"
  | "running"
  | "cancel_requested"
  | "cancelled"
  | "interrupted"
  | "not_run"
  | "unknown";

export interface Usage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

export interface SourceSpan {
  path?: string;
  start_line: number;
  end_line: number;
  start_char?: number;
  end_char?: number;
}

export interface Card {
  material_id: string;
  clean_title: string;
  extract: string;
  content_kind: string;
  heading_path: string[];
  source_span: SourceSpan;
  confidence_flags: string[];
  quality_flags: string[];
}

export interface Citation {
  material_id: string;
  quote: string;
  source_ref: SourceSpan;
  card_title: string;
  confidence_flags?: string[];
  quality_flags?: string[];
}

export interface Evidence {
  evidence_unit_id: string;
  claim: string;
  evidence_type: string;
  relevance: string;
  confidence: string;
  caveats: string[];
  citations: Citation[];
  paper_id?: string;
  paper_title?: string;
  source_run_id?: string;
}

export interface EvidenceFailure {
  error_code: string;
  error_message: string;
  material_id: string;
  card_title: string;
  source_span: SourceSpan;
  material?: Card;
}

export interface AnalysisSummary {
  run_id: string | null;
  status: Status;
  paper_relevance: string | null;
  source_material_count: number;
  selected_material_count: number;
  evidence_unit_count: number;
  evidence_failure_count: number;
  revisit_status: string | null;
  request_count: number;
  usage: Usage;
  failure_codes: string[];
}

export interface PaperSummary {
  paper_id: string;
  workspace_paper_id: string;
  paper_title: string;
  source: string;
  card: {
    status: Status;
    generation_id: string | null;
    structure_quality: string | null;
    block_count: number;
    material_count: number;
    issue_count: number;
    coverage: Record<string, unknown>;
    content_kinds: string[];
  };
  analysis: AnalysisSummary;
}

export interface JobPaperResult {
  paper_id: string;
  workspace_paper_id: string;
  status: "completed" | "failed";
  started_at: string;
  finished_at: string;
  generation_id: string | null;
  structure_quality?: string | null;
  material_count?: number;
  issue_count?: number;
  analysis_status?: Status;
  analysis_run_id?: string;
  paper_relevance?: string | null;
  selected_material_count?: number;
  evidence_unit_count?: number;
  evidence_failure_count?: number;
  request_count?: number;
  usage?: Usage;
  failure_code: string | null;
  failure_message: string | null;
}

export interface PipelineJob {
  schema_version: "review_ui_job.v1";
  job_id: string;
  job_type:
    | "card_build"
    | "topic_brief"
    | "topic_synthesis"
    | "full_pipeline";
  project_id: string;
  project_revision: number;
  status:
    | "queued"
    | "running"
    | "cancel_requested"
    | "completed"
    | "completed_with_failures"
    | "cancelled"
    | "interrupted"
    | "failed";
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  progress: {
    total: number;
    completed: number;
    succeeded: number;
    failed: number;
    current_paper_id: string | null;
  };
  paper_results: JobPaperResult[];
  result?: {
    status: "completed" | "failed";
    synthesis_run_id?: string;
    pipeline_run_id?: string;
    paper_count?: number;
    included_paper_count?: number;
    excluded_paper_count?: number;
    completed_card_count?: number;
    source_paper_count?: number;
    source_evidence_count?: number;
    theme_count?: number;
    synthesis_unit_count?: number;
    section_count?: number;
    request_count?: number;
    usage?: Usage;
    failure_code?: string | null;
    failure_message?: string | null;
  } | null;
  failure_code: string | null;
  failure_message: string | null;
}

export type CardJob = PipelineJob;

export interface JobRetryCandidates {
  job_id: string;
  job_type: "card_build" | "topic_brief";
  project_id: string;
  paper_ids: string[];
  completed_paper_ids: string[];
  candidate_count: number;
}

export interface ImportSourceResult {
  index: number;
  paper_id: string;
  original_filename: string;
  status: "imported" | "skipped_duplicate" | "failed";
  sha256: string | null;
  size_bytes: number | null;
  duplicate_of_paper_id: string | null;
  error_code: string | null;
  message: string;
}

export interface ImportSourcesResponse {
  project: Record<string, unknown>;
  imported_paper_ids: string[];
  collection_path: string | null;
  results: ImportSourceResult[];
  counts: {
    imported: number;
    skipped_duplicate: number;
    failed: number;
  };
  summary: Project;
}

export interface CardJobPreflight {
  schema_version: "review_ui_card_job_input.v1";
  project_id: string;
  project_revision: number;
  topic: string;
  collection_path: string;
  collection_sha256: string;
  pdf_provider: "none" | "mineru";
  uses_external_service: boolean;
  papers: Array<{
    paper_id: string;
    workspace_paper_id: string;
    source_path: string;
    source_sha256: string;
    source_size_bytes: number;
    source_suffix: string;
  }>;
  commands: Array<{ paper_id: string; argv: string[] }>;
  created_at: string;
}

export interface TopicBriefJobPreflight {
  schema_version: "review_ui_topic_brief_job_input.v1";
  project_id: string;
  project_revision: number;
  topic: string;
  uses_external_service: true;
  external_service: string;
  model_profile_id: string;
  model: string;
  thinking: { type: string };
  model_profile_sha256: string;
  review_config_sha256: string;
  papers: Array<{
    paper_id: string;
    workspace_paper_id: string;
    analysis_run_id: string;
    generation_id: string;
    material_count: number;
    input_sha256: string;
    materials_sha256: string;
    materials_size_bytes: number;
  }>;
  commands: Array<{
    paper_id: string;
    analysis_run_id: string;
    argv: string[];
  }>;
  created_at: string;
}

export interface TopicSynthesisJobPreflight {
  schema_version: "review_ui_topic_synthesis_job_input.v1";
  project_id: string;
  project_revision: number;
  topic: string;
  uses_external_service: true;
  external_service: string;
  model_profile_id: string;
  model: string;
  thinking: { type: string };
  synthesis_run_id: string;
  collection_sha256: string;
  input_sha256: string;
  source_paper_count: number;
  source_evidence_count: number;
  source_runs: Array<{
    run_id: string;
    status: Status;
    paper_id: string;
    paper_title: string;
    generation_id: string;
    paper_relevance: string;
    evidence_unit_count: number;
    manifest_sha256: string;
  }>;
  created_at: string;
}

export interface FullPipelineJobPreflight {
  schema_version: "review_ui_full_pipeline_job_input.v1";
  project_id: string;
  project_revision: number;
  topic: string;
  uses_external_service: true;
  external_services: string[];
  model_profile_id: string;
  model: string;
  thinking: { type: string };
  model_profile_sha256: string;
  review_config_schema: string;
  review_config_sha256: string;
  synthesis_config_schema: string;
  synthesis_config_sha256: string;
  pipeline_run_id: string;
  pdf_provider: "none" | "mineru";
  collection_sha256: string;
  papers: Array<{
    paper_id: string;
    workspace_paper_id: string;
    source_path: string;
    source_sha256: string;
    source_size_bytes: number;
    source_suffix: string;
  }>;
  created_at: string;
}

export interface SynthesisSummary {
  run_id: string | null;
  status: Status;
  theme_count: number;
  synthesis_unit_count: number;
  section_count: number;
  source_evidence_count: number;
  multi_theme_evidence_count: number;
  theme_unassigned_count: number;
  assigned_but_unused_count: number;
  partial_source_count: number;
  usage: Usage;
}

export interface ProjectSummary {
  schema_version: string;
  project_id: string;
  name: string;
  description: string;
  topic: string;
  revision: number;
  current_collection_path: string | null;
  paper_count: number;
  card_count: number;
  evidence_count: number;
  analysis_status_counts: Record<string, number>;
  synthesis: SynthesisSummary;
}

export interface Project extends ProjectSummary {
  papers: PaperSummary[];
}

export interface PaperDetail {
  summary: PaperSummary;
  document: {
    path: string;
    line_count: number;
    lines: string[];
  };
  cards: Card[];
  evidence: Evidence[];
  failures: EvidenceFailure[];
  brief_markdown: string;
  issues: Array<{
    code: string;
    message: string;
    severity: string;
  }>;
  coverage: Record<string, unknown>;
}

export interface ThemeAssignment {
  evidence_unit_id: string;
  role: string;
}

export interface Theme {
  theme_id: string;
  title: string;
  focus: string;
  synthesis_value: string;
  relation_type: string;
  assignments: ThemeAssignment[];
}

export interface SynthesisUnit {
  synthesis_unit_id: string;
  unit_type: string;
  synthesis_statement: string;
  theme_ids: string[];
  evidence_unit_ids: string[];
  gap_ids: string[];
}

export interface OutlineParagraph {
  synthesis_move: string;
  paragraph_role: string;
  evidence_unit_ids: string[];
  gap_ids: string[];
  synthesis_unit_ids: string[];
}

export interface OutlineSection {
  title: string;
  purpose: string;
  theme_ids: string[];
  paragraphs: OutlineParagraph[];
}

export interface SynthesisDetail {
  run_id: string;
  manifest: Record<string, unknown>;
  output: {
    topic: string;
    source_runs: Array<Record<string, unknown>>;
    evidence_map: {
      themes: Theme[];
      unassigned_evidence: Array<{
        evidence_unit_id: string;
        reason_code: string;
        reason: string;
      }>;
    };
    outline: {
      synthesis_units: SynthesisUnit[];
      sections: OutlineSection[];
      corpus_gaps: Array<Record<string, unknown>>;
      prioritized_retrieval_directions: Array<Record<string, unknown>>;
    };
    coverage: Record<string, unknown>;
  };
  evidence_catalog: Record<string, Evidence>;
  report_markdown: string;
}

export interface RunRecord {
  run_id: string;
  run_type: string;
  schema_version: string;
  status: Status;
  topic: string;
  paper_id?: string;
  project_id?: string;
  started_at?: string;
  finished_at?: string;
  request_count: number;
  total_tokens: number;
  failure_count: number;
  failure_codes: string[];
}

export interface RunComparisonDifference {
  code: string;
  message: string;
  paper_id: string | null;
  field: string;
  left: unknown;
  right: unknown;
}

export interface RunComparisonMetrics {
  status: Status;
  duration_seconds: number | null;
  request_count: number;
  total_tokens: number;
  failure_count: number;
  output: Record<string, number>;
}

export interface RunComparison {
  schema_version: "review_ui_run_comparison.v1";
  project_id: string;
  job_type:
    | "card_build"
    | "topic_brief"
    | "topic_synthesis"
    | "full_pipeline";
  comparable: boolean;
  verdict: "same_frozen_input" | "input_changed";
  input_differences: RunComparisonDifference[];
  configuration_differences: RunComparisonDifference[];
  left: {
    job_id: string;
    input_schema: string;
    metrics: RunComparisonMetrics;
  };
  right: {
    job_id: string;
    input_schema: string;
    metrics: RunComparisonMetrics;
  };
  deltas: {
    duration_seconds: number | null;
    request_count: number;
    total_tokens: number;
    failure_count: number;
    output: Record<string, number>;
  } | null;
}
