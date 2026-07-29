import type {
  CardJob,
  CardJobPreflight,
  FullPipelineJobPreflight,
  ImportSourcesResponse,
  PaperDetail,
  Project,
  ProjectSummary,
  RunComparison,
  RunRecord,
  SynthesisDetail,
  TopicBriefJobPreflight,
  TopicSynthesisJobPreflight,
} from "./types";

export class ApiError extends Error {
  code: string;

  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

async function fetchJson<T>(
  url: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json();
  if (!response.ok) {
    throw new ApiError(
      payload.error_code ?? "ui.request_failed",
      payload.error_message ?? payload.detail ?? "请求失败",
    );
  }
  return payload as T;
}

export const api = {
  projects: () => fetchJson<ProjectSummary[]>("/api/projects"),
  createProject: (input: {
    project_id: string;
    name: string;
    topic: string;
    description: string;
  }) =>
    fetchJson<Project>("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  project: (projectId: string) =>
    fetchJson<Project>(`/api/projects/${encodeURIComponent(projectId)}`),
  updateProject: (
    projectId: string,
    input: {
      expected_revision: number;
      name?: string;
      topic?: string;
      description?: string;
    },
  ) =>
    fetchJson<Project>(`/api/projects/${encodeURIComponent(projectId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  importSources: (
    projectId: string,
    expectedRevision: number,
    sources: Array<{ file: File; paperId: string }>,
  ) => {
    const form = new FormData();
    for (const source of sources) form.append("files", source.file);
    form.append(
      "paper_ids",
      JSON.stringify(sources.map((source) => source.paperId)),
    );
    form.append("expected_revision", String(expectedRevision));
    return fetchJson<ImportSourcesResponse>(
      `/api/projects/${encodeURIComponent(projectId)}/sources`,
      {
      method: "POST",
      body: form,
      },
    );
  },
  preflightCardJob: (
    projectId: string,
    input: {
      expected_revision: number;
      paper_ids: string[];
      pdf_provider: "none" | "mineru";
    },
  ) =>
    fetchJson<CardJobPreflight>(
      `/api/projects/${encodeURIComponent(projectId)}/card-jobs/preflight`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    ),
  createCardJob: (
    projectId: string,
    input: {
      expected_revision: number;
      paper_ids: string[];
      pdf_provider: "none" | "mineru";
    },
  ) =>
    fetchJson<CardJob>(
      `/api/projects/${encodeURIComponent(projectId)}/card-jobs`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    ),
  preflightTopicBriefJob: (
    projectId: string,
    input: {
      expected_revision: number;
      paper_ids: string[];
      external_service_confirmed: boolean;
    },
  ) =>
    fetchJson<TopicBriefJobPreflight>(
      `/api/projects/${encodeURIComponent(projectId)}/topic-brief-jobs/preflight`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    ),
  createTopicBriefJob: (
    projectId: string,
    input: {
      expected_revision: number;
      paper_ids: string[];
      external_service_confirmed: boolean;
    },
  ) =>
    fetchJson<CardJob>(
      `/api/projects/${encodeURIComponent(projectId)}/topic-brief-jobs`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    ),
  preflightTopicSynthesisJob: (
    projectId: string,
    input: {
      expected_revision: number;
      source_run_ids: string[];
      external_service_confirmed: boolean;
    },
  ) =>
    fetchJson<TopicSynthesisJobPreflight>(
      `/api/projects/${encodeURIComponent(projectId)}/topic-synthesis-jobs/preflight`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    ),
  createTopicSynthesisJob: (
    projectId: string,
    input: {
      expected_revision: number;
      source_run_ids: string[];
      external_service_confirmed: boolean;
    },
  ) =>
    fetchJson<CardJob>(
      `/api/projects/${encodeURIComponent(projectId)}/topic-synthesis-jobs`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    ),
  preflightFullPipelineJob: (
    projectId: string,
    input: {
      expected_revision: number;
      external_service_confirmed: boolean;
    },
  ) =>
    fetchJson<FullPipelineJobPreflight>(
      `/api/projects/${encodeURIComponent(projectId)}/full-pipeline-jobs/preflight`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    ),
  createFullPipelineJob: (
    projectId: string,
    input: {
      expected_revision: number;
      external_service_confirmed: boolean;
    },
  ) =>
    fetchJson<CardJob>(
      `/api/projects/${encodeURIComponent(projectId)}/full-pipeline-jobs`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    ),
  jobs: (projectId: string) =>
    fetchJson<CardJob[]>(
      `/api/jobs?project_id=${encodeURIComponent(projectId)}`,
    ),
  job: (jobId: string) =>
    fetchJson<CardJob>(`/api/jobs/${encodeURIComponent(jobId)}`),
  jobLog: (jobId: string) =>
    fetchJson<{
      job_id: string;
      after_line: number;
      next_line: number;
      lines: string[];
    }>(`/api/jobs/${encodeURIComponent(jobId)}/log`),
  paper: (projectId: string, paperId: string) =>
    fetchJson<PaperDetail>(
      `/api/projects/${encodeURIComponent(projectId)}/papers/${encodeURIComponent(paperId)}`,
    ),
  synthesis: (projectId: string) =>
    fetchJson<SynthesisDetail>(
      `/api/projects/${encodeURIComponent(projectId)}/synthesis`,
    ),
  runs: (projectId?: string) =>
    fetchJson<RunRecord[]>(
      `/api/runs${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`,
    ),
  runComparison: (
    projectId: string,
    leftJobId: string,
    rightJobId: string,
  ) =>
    fetchJson<RunComparison>(
      `/api/projects/${encodeURIComponent(projectId)}/run-comparison?left_job_id=${encodeURIComponent(leftJobId)}&right_job_id=${encodeURIComponent(rightJobId)}`,
    ),
  systemStatus: () =>
    fetchJson<{
      mode: string;
      workspace: string;
      workspace_available: boolean;
      project_config_root: string;
      project_count: number;
      job_count: number;
      active_job_count: number;
    }>("/api/system/status"),
};
