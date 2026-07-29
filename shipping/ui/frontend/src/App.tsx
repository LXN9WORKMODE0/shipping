import {
  useEffect,
  useMemo,
  useState,
  type ChangeEvent,
  type FormEvent,
  type ReactNode,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  Boxes,
  CheckCircle2,
  ChevronRight,
  CircleMinus,
  Clock3,
  Database,
  FileSearch,
  Files,
  Filter,
  GitBranch,
  GitCompareArrows,
  Layers3,
  ListTree,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Search,
  Server,
  ShieldAlert,
  Square,
  Upload,
  X,
  XCircle,
} from "lucide-react";
import {
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useParams,
} from "react-router-dom";
import { api, ApiError } from "./api";
import type {
  Card,
  CardJob,
  Evidence,
  EvidenceFailure,
  ImportSourcesResponse,
  PaperDetail,
  Project,
  ProjectSummary,
  RunRecord,
  RunComparison,
  SourceSpan,
  Status,
  SynthesisDetail,
} from "./types";

const PROJECT_ID = "adversarial-8papers-20260728";

const statusLabels: Record<string, string> = {
  completed: "已完成",
  completed_with_failures: "部分 Evidence 失败",
  completed_with_revisit_failure: "回查未闭合",
  excluded: "与主题无关",
  failed: "失败",
  queued: "排队中",
  running: "运行中",
  cancel_requested: "取消中",
  cancelled: "已取消",
  interrupted: "服务中断",
  not_run: "未运行",
  unknown: "未知",
};

const relevanceLabels: Record<string, string> = {
  core: "核心",
  supporting: "支撑",
  peripheral: "外围",
  excluded: "无关",
};

const relationLabels: Record<string, string> = {
  convergent: "一致",
  complementary: "互补",
  divergent: "分歧",
  single_source: "单一来源",
  consensus: "共识",
  complement: "互补",
  contrast: "对照",
  causal_chain: "机制链",
  single_source_context: "单篇背景",
  corpus_gap: "语料缺口",
};

function statusTone(status: string) {
  if (status === "completed") return "success";
  if (
    status === "completed_with_failures" ||
    status === "completed_with_revisit_failure" ||
    status === "cancel_requested" ||
    status === "interrupted" ||
    status === "running"
  )
    return "warning";
  if (status === "failed") return "danger";
  return "neutral";
}

function StatusBadge({
  status,
  label,
}: {
  status: Status | string;
  label?: string;
}) {
  const tone = statusTone(status);
  const Icon =
    tone === "success"
      ? CheckCircle2
      : tone === "warning"
        ? AlertTriangle
        : tone === "danger"
          ? XCircle
          : CircleMinus;
  return (
    <span className={`status-badge status-${tone}`}>
      <Icon size={13} />
      {label ?? statusLabels[status] ?? status}
    </span>
  );
}

function LoadingBlock({ label = "正在读取产物" }: { label?: string }) {
  return (
    <div className="state-block">
      <Activity className="spin" size={20} />
      <span>{label}</span>
    </div>
  );
}

function ErrorBlock({ error }: { error: unknown }) {
  const apiError = error instanceof ApiError ? error : null;
  return (
    <div className="error-block">
      <ShieldAlert size={20} />
      <div>
        <strong>{apiError?.code ?? "ui.unexpected_error"}</strong>
        <p>{apiError?.message ?? "读取界面数据时发生未知错误。"}</p>
      </div>
    </div>
  );
}

function Shell({ children }: { children: ReactNode }) {
  const navItems = [
    { to: "/", label: "综述任务", icon: Files, end: true },
    { to: "/runs", label: "运行记录", icon: Activity },
    { to: "/system", label: "系统状态", icon: Server },
  ];
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <ListTree size={20} />
          </div>
          <div>
            <strong>综述工作台</strong>
            <span>本地工作流</span>
          </div>
        </div>
        <nav>
          {navItems.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              <Icon size={17} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <Database size={15} />
          <span>workspace 实时读取</span>
        </div>
      </aside>
      <main className="main-content">{children}</main>
    </div>
  );
}

function PageHeader({
  eyebrow,
  title,
  description,
  backTo,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  backTo?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="page-title-row">
        {backTo && (
          <Link className="icon-button" to={backTo} title="返回">
            <ArrowLeft size={18} />
          </Link>
        )}
        <div>
          {eyebrow && <span className="eyebrow">{eyebrow}</span>}
          <h1>{title}</h1>
          {description && <p>{description}</p>}
        </div>
        {actions && <div className="page-actions">{actions}</div>}
      </div>
    </header>
  );
}

function ProjectsPage() {
  const [createOpen, setCreateOpen] = useState(false);
  const query = useQuery({
    queryKey: ["projects"],
    queryFn: api.projects,
  });
  if (query.isLoading) return <LoadingBlock />;
  if (query.error) return <ErrorBlock error={query.error} />;
  const projects = query.data ?? [];
  return (
    <>
      <PageHeader
        eyebrow="本地 Pipeline"
        title="综述任务"
        description="查看每个主题下论文、Card、Evidence 和综合结果的当前状态。"
      />
      <section className="content-section">
        <div className="section-heading">
          <div>
            <h2>当前任务</h2>
            <p>{projects.length} 个显式配置任务</p>
          </div>
          <button
            type="button"
            className="primary-button"
            onClick={() => setCreateOpen(true)}
          >
            <Plus size={15} />
            新建任务
          </button>
        </div>
        <div className="project-list">
          {projects.map((project) => (
            <ProjectRow key={project.project_id} project={project} />
          ))}
        </div>
      </section>
      {createOpen && (
        <ProjectEditorDialog onClose={() => setCreateOpen(false)} />
      )}
    </>
  );
}

function ProjectRow({ project }: { project: ProjectSummary }) {
  return (
    <Link className="project-row" to={`/projects/${project.project_id}`}>
      <div className="project-primary">
        <span className="project-icon">
          <BookOpen size={19} />
        </span>
        <div>
          <strong>{project.name}</strong>
          <p>{project.topic}</p>
        </div>
      </div>
      <div className="project-stat">
        <span>论文</span>
        <strong>{project.paper_count}</strong>
      </div>
      <div className="project-stat">
        <span>Card</span>
        <strong>{project.card_count.toLocaleString("zh-CN")}</strong>
      </div>
      <div className="project-stat">
        <span>Evidence</span>
        <strong>{project.evidence_count}</strong>
      </div>
      <div className="project-status">
        <StatusBadge status={project.synthesis.status} />
        <ChevronRight size={18} />
      </div>
    </Link>
  );
}

function ProjectEditorDialog({
  project,
  onClose,
}: {
  project?: Project;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [projectId, setProjectId] = useState(
    project?.project_id ?? defaultProjectId(),
  );
  const [name, setName] = useState(project?.name ?? "");
  const [topic, setTopic] = useState(project?.topic ?? "");
  const [description, setDescription] = useState(project?.description ?? "");
  const mutation = useMutation({
    mutationFn: () =>
      project
        ? api.updateProject(project.project_id, {
            expected_revision: project.revision,
            name,
            topic,
            description,
          })
        : api.createProject({
            project_id: projectId,
            name,
            topic,
            description,
          }),
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
      await queryClient.invalidateQueries({
        queryKey: ["project", saved.project_id],
      });
      onClose();
      if (!project) navigate(`/projects/${saved.project_id}`);
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    mutation.mutate();
  };
  return (
    <div className="dialog-backdrop" role="presentation">
      <section
        className="dialog-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="project-dialog-title"
      >
        <div className="dialog-header">
          <div>
            <span>{project ? "项目元数据" : "M2.1 项目存储"}</span>
            <h2 id="project-dialog-title">
              {project ? "编辑综述任务" : "新建综述任务"}
            </h2>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            title="关闭"
          >
            <X size={17} />
          </button>
        </div>
        <form onSubmit={submit}>
          <label className="form-field">
            <span>项目 ID</span>
            <input
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
              disabled={Boolean(project)}
              pattern="[a-z][a-z0-9-]{2,63}"
              required
            />
            <small>小写字母开头，可包含数字和连字符；创建后不可修改。</small>
          </label>
          <label className="form-field">
            <span>任务名称</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
          </label>
          <label className="form-field">
            <span>综述主题</span>
            <textarea
              value={topic}
              onChange={(event) => setTopic(event.target.value)}
              required
              rows={3}
            />
          </label>
          <label className="form-field">
            <span>说明</span>
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={2}
            />
          </label>
          {mutation.error && <InlineError error={mutation.error} />}
          <div className="dialog-actions">
            <button type="button" className="secondary-button" onClick={onClose}>
              取消
            </button>
            <button
              type="submit"
              className="primary-button"
              disabled={mutation.isPending}
            >
              {mutation.isPending
                ? "正在保存"
                : project
                  ? "保存修改"
                  : "创建任务"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function ImportSourcesDialog({
  project,
  onClose,
}: {
  project: Project;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [sources, setSources] = useState<
    Array<{ file: File; paperId: string }>
  >([]);
  const [importResult, setImportResult] =
    useState<ImportSourcesResponse | null>(null);
  const mutation = useMutation({
    mutationFn: () =>
      api.importSources(project.project_id, project.revision, sources),
    onSuccess: async (result) => {
      setImportResult(result);
      setSources([]);
      await queryClient.invalidateQueries({
        queryKey: ["project", project.project_id],
      });
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  const chooseFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    setImportResult(null);
    mutation.reset();
    setSources(
      files.map((file) => ({
        file,
        paperId: file.name.replace(/\.(pdf|md|markdown|txt)$/i, ""),
      })),
    );
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    mutation.mutate();
  };
  return (
    <div className="dialog-backdrop" role="presentation">
      <section
        className="dialog-panel dialog-wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="import-dialog-title"
      >
        <div className="dialog-header">
          <div>
            <span>revision {project.revision}</span>
            <h2 id="import-dialog-title">批量导入论文来源</h2>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            title="关闭"
          >
            <X size={17} />
          </button>
        </div>
        <form onSubmit={submit}>
          <label className="file-drop">
            <Upload size={23} />
            <strong>选择 PDF、Markdown 或文本文件</strong>
            <span>文件复制到项目受管目录，并记录原文件名、大小和 SHA-256。</span>
            <input
              type="file"
              accept=".pdf,.md,.markdown,.txt"
              multiple
              onChange={chooseFiles}
            />
          </label>
          {sources.length > 0 && (
            <div className="source-draft-list">
              <div className="source-draft-head">
                <span>来源文件</span>
                <span>稳定论文 ID</span>
              </div>
              {sources.map((source, index) => (
                <div className="source-draft-row" key={`${source.file.name}-${index}`}>
                  <div>
                    <strong>{source.file.name}</strong>
                    <span>{formatBytes(source.file.size)}</span>
                  </div>
                  <input
                    value={source.paperId}
                    required
                    onChange={(event) =>
                      setSources((current) =>
                        current.map((row, rowIndex) =>
                          rowIndex === index
                            ? { ...row, paperId: event.target.value }
                            : row,
                        ),
                      )
                    }
                  />
                </div>
              ))}
            </div>
          )}
          {mutation.error && <InlineError error={mutation.error} />}
          {importResult && (
            <div className="import-report">
              <div className="import-report-summary">
                <div>
                  <span>已导入</span>
                  <strong>{importResult.counts.imported}</strong>
                </div>
                <div>
                  <span>重复跳过</span>
                  <strong>{importResult.counts.skipped_duplicate}</strong>
                </div>
                <div>
                  <span>失败</span>
                  <strong>{importResult.counts.failed}</strong>
                </div>
              </div>
              <div className="import-result-list">
                {importResult.results.map((result) => (
                  <div
                    className={`import-result-row import-result-${result.status}`}
                    key={`${result.index}-${result.original_filename}`}
                  >
                    <StatusBadge
                      status={
                        result.status === "imported"
                          ? "completed"
                          : result.status === "failed"
                            ? "failed"
                            : "completed_with_failures"
                      }
                      label={
                        result.status === "imported"
                          ? "已导入"
                          : result.status === "failed"
                            ? "失败"
                            : "重复跳过"
                      }
                    />
                    <div>
                      <strong>{result.original_filename}</strong>
                      <span>{result.paper_id || "未提供论文 ID"}</span>
                    </div>
                    <p>
                      {result.message}
                      {result.duplicate_of_paper_id
                        ? ` 对应：${result.duplicate_of_paper_id}`
                        : ""}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}
          <p className="form-note">
            文件逐项校验。有效文件加入论文池；重复项和失败项会明确列出，不阻塞同批其他文件。
          </p>
          <div className="dialog-actions">
            <button type="button" className="secondary-button" onClick={onClose}>
              {importResult ? "关闭" : "取消"}
            </button>
            <button
              type="submit"
              className="primary-button"
              disabled={sources.length === 0 || mutation.isPending}
            >
              {mutation.isPending ? "正在导入" : `导入 ${sources.length} 篇`}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function InlineError({ error }: { error: unknown }) {
  const apiError = error instanceof ApiError ? error : null;
  return (
    <div className="inline-error">
      <AlertTriangle size={15} />
      <div>
        <code>{apiError?.code ?? "ui.request_failed"}</code>
        <p>{apiError?.message ?? "请求失败。"}</p>
      </div>
    </div>
  );
}

function ProjectPage() {
  const { projectId = PROJECT_ID } = useParams();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [analysisFilter, setAnalysisFilter] = useState("all");
  const [importOpen, setImportOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [cardJobOpen, setCardJobOpen] = useState(false);
  const [analysisJobOpen, setAnalysisJobOpen] = useState(false);
  const [synthesisJobOpen, setSynthesisJobOpen] = useState(false);
  const [fullPipelineOpen, setFullPipelineOpen] = useState(false);
  const [selectedPaperIds, setSelectedPaperIds] = useState<Set<string>>(
    new Set(),
  );
  const query = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.project(projectId),
  });
  const jobsQuery = useQuery({
    queryKey: ["jobs", projectId],
    queryFn: () => api.jobs(projectId),
    refetchInterval: 1000,
  });
  const latestJob = jobsQuery.data?.[0];
  const latestPaperJob =
    jobsQuery.data?.find(
      (job) =>
        job.job_type === "card_build" || job.job_type === "topic_brief",
    ) ?? null;
  const retryableJob =
    latestPaperJob &&
    !["queued", "running", "cancel_requested", "completed"].includes(
      latestPaperJob.status,
    )
      ? latestPaperJob
      : null;
  const retryCandidatesQuery = useQuery({
    queryKey: ["job-retry-candidates", retryableJob?.job_id],
    queryFn: () => api.retryCandidates(retryableJob!.job_id),
    enabled: Boolean(retryableJob),
    retry: false,
  });
  useEffect(() => {
    if (
      latestJob?.status === "completed" ||
      latestJob?.status === "completed_with_failures" ||
      latestJob?.status === "cancelled" ||
      latestJob?.status === "interrupted" ||
      latestJob?.status === "failed"
    ) {
      void queryClient.invalidateQueries({
        queryKey: ["project", projectId],
      });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    }
  }, [latestJob?.job_id, latestJob?.status, projectId, queryClient]);
  if (query.isLoading) return <LoadingBlock />;
  if (query.error) return <ErrorBlock error={query.error} />;
  const project = query.data as Project;
  const papers = project.papers.filter((paper) => {
    const matchesSearch =
      paper.paper_title.toLowerCase().includes(search.toLowerCase()) ||
      paper.paper_id.toLowerCase().includes(search.toLowerCase());
    return (
      matchesSearch &&
      (analysisFilter === "all" ||
        (analysisFilter === "processing_failed"
          ? paper.card.status === "failed" ||
            paper.analysis.status === "failed"
          : analysisFilter === "card_ready"
            ? paper.card.status === "completed"
            : paper.analysis.status === analysisFilter))
    );
  });
  const selectedPapers = project.papers.filter((paper) =>
    selectedPaperIds.has(paper.paper_id),
  );
  const selectedCardsReady =
    selectedPapers.length > 0 &&
    selectedPapers.every((paper) => paper.card.status === "completed");
  const synthesisSources = project.papers.filter(
    (paper) =>
      paper.analysis.run_id &&
      paper.analysis.status !== "excluded" &&
      paper.analysis.status !== "failed",
  );
  const activeJob = jobsQuery.data?.some(
    (job) =>
      job.status === "queued" ||
      job.status === "running" ||
      job.status === "cancel_requested",
  );
  const selectVisible = () =>
    setSelectedPaperIds(new Set(papers.map((paper) => paper.paper_id)));
  const selectPending = () =>
    setSelectedPaperIds(
      new Set(
        project.papers
          .filter((paper) => paper.card.status !== "completed")
          .map((paper) => paper.paper_id),
      ),
    );
  const selectCardReady = () =>
    setSelectedPaperIds(
      new Set(
        project.papers
          .filter((paper) => paper.card.status === "completed")
          .map((paper) => paper.paper_id),
      ),
    );
  const selectRetryCandidates = () => {
    const existingIds = new Set(
      project.papers.map((paper) => paper.paper_id),
    );
    setSelectedPaperIds(
      new Set(
        (retryCandidatesQuery.data?.paper_ids ?? []).filter((paperId) =>
          existingIds.has(paperId),
        ),
      ),
    );
  };
  return (
    <>
      <PageHeader
        eyebrow="综述任务"
        title={project.name}
        description={project.topic}
        actions={
          <>
            <button
              type="button"
              className="secondary-button"
              onClick={() => setEditOpen(true)}
            >
              <Pencil size={15} />
              编辑任务
            </button>
            <button
              type="button"
              className="primary-button"
              onClick={() => setImportOpen(true)}
            >
              <Upload size={15} />
              导入论文
            </button>
          </>
        }
      />
      <section className="stage-strip">
        <div className="stage-item">
          <span className="stage-index">1</span>
          <div>
            <span>论文与 Card</span>
            <strong>
              {project.papers.filter((row) => row.card.status === "completed").length}
              {" / "}
              {project.paper_count} 篇完成
            </strong>
          </div>
          {project.paper_count > 0 &&
          project.papers.every((row) => row.card.status === "completed") ? (
            <CheckCircle2 size={18} />
          ) : (
            <CircleMinus size={18} className="muted-icon" />
          )}
        </div>
        <div className="stage-item">
          <span className="stage-index">2</span>
          <div>
            <span>单篇主题分析</span>
            <strong>{project.evidence_count} 条 Evidence</strong>
          </div>
          <AlertTriangle size={18} className="amber" />
        </div>
        {project.synthesis.status === "not_run" ? (
          <div className="stage-item stage-disabled">
            <span className="stage-index">3</span>
            <div>
              <span>跨论文综合</span>
              <strong>尚未运行</strong>
            </div>
            <CircleMinus size={18} />
          </div>
        ) : (
          <Link
            className="stage-item stage-link"
            to={`/projects/${project.project_id}/synthesis`}
          >
            <span className="stage-index">3</span>
            <div>
              <span>跨论文综合</span>
              <strong>
                {project.synthesis.theme_count} 个主题 ·{" "}
                {project.synthesis.section_count} 章
              </strong>
            </div>
            <ChevronRight size={18} />
          </Link>
        )}
      </section>
      <section className="metrics-band">
        <Metric label="论文" value={project.paper_count} />
        <Metric label="Card" value={project.card_count} />
        <Metric label="Evidence" value={project.evidence_count} />
        <Metric
          label="部分状态论文"
          value={
            (project.analysis_status_counts.completed_with_failures ?? 0) +
            (project.analysis_status_counts
              .completed_with_revisit_failure ?? 0)
          }
          tone="warning"
        />
        <Metric
          label="多主题 Evidence"
          value={project.synthesis.multi_theme_evidence_count}
        />
        <Metric
          label="未进入提纲"
          value={project.synthesis.assigned_but_unused_count}
          tone="warning"
        />
      </section>
      {jobsQuery.error && <InlineError error={jobsQuery.error} />}
      {latestJob && <JobPanel job={latestJob} />}
      <section className="content-section paper-table-section">
        <div className="toolbar">
          <label className="search-field">
            <Search size={16} />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索论文"
            />
          </label>
          <label className="filter-field">
            <Filter size={15} />
            <select
              value={analysisFilter}
              onChange={(event) => setAnalysisFilter(event.target.value)}
            >
              <option value="all">全部分析状态</option>
              <option value="card_ready">Card 已就绪</option>
              <option value="processing_failed">处理失败</option>
              <option value="completed">已完成</option>
              <option value="completed_with_failures">
                部分 Evidence 失败
              </option>
              <option value="completed_with_revisit_failure">
                回查未闭合
              </option>
              <option value="failed">失败</option>
            </select>
          </label>
          <button
            type="button"
            className="secondary-button compact-button"
            onClick={selectVisible}
            disabled={papers.length === 0}
          >
            选择当前
          </button>
          <button
            type="button"
            className="secondary-button compact-button"
            onClick={selectPending}
            disabled={project.paper_count === 0}
          >
            选择未制卡
          </button>
          <button
            type="button"
            className="secondary-button compact-button"
            onClick={selectCardReady}
            disabled={
              !project.papers.some(
                (paper) => paper.card.status === "completed",
              )
            }
          >
            选择已制卡
          </button>
          {retryableJob && (
            <button
              type="button"
              className="secondary-button compact-button"
              onClick={selectRetryCandidates}
              disabled={
                retryCandidatesQuery.isLoading ||
                !retryCandidatesQuery.data?.candidate_count
              }
            >
              <RefreshCw size={14} />
              选择上次
              {retryableJob.job_type === "card_build" ? "制卡" : "分析"}
              未成功
            </button>
          )}
          {selectedPaperIds.size > 0 && (
            <button
              type="button"
              className="secondary-button compact-button"
              onClick={() => setSelectedPaperIds(new Set())}
            >
              清除选择
            </button>
          )}
          <span className="toolbar-count">
            已选 {selectedPaperIds.size} / {papers.length} 篇
          </span>
          <button
            type="button"
            className="primary-button"
            onClick={() => setCardJobOpen(true)}
            disabled={selectedPaperIds.size === 0 || activeJob}
            title={activeJob ? "已有 Card Job 正在运行" : "运行 Card"}
          >
            <Play size={14} />
            运行 Card
          </button>
          <button
            type="button"
            className="primary-button"
            onClick={() => setAnalysisJobOpen(true)}
            disabled={!selectedCardsReady || activeJob}
            title={
              activeJob
                ? "已有 Job 正在运行"
                : selectedCardsReady
                  ? "运行单篇主题分析"
                  : "所选论文必须先完成 Card"
            }
          >
            <FileSearch size={14} />
            分析论文
          </button>
          <button
            type="button"
            className="primary-button"
            onClick={() => setSynthesisJobOpen(true)}
            disabled={synthesisSources.length < 2 || activeJob}
            title={
              activeJob
                ? "已有 Job 正在运行"
                : synthesisSources.length < 2
                  ? "至少需要两个可综合的单篇分析 run"
                  : "运行跨论文综合"
            }
          >
            <GitBranch size={14} />
            跨论文综合
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => setFullPipelineOpen(true)}
            disabled={project.paper_count < 2 || activeJob}
            title={
              activeJob
                ? "已有 Job 正在运行"
                : project.paper_count < 2
                  ? "完整流程至少需要两篇论文"
                  : "严格使用全部项目论文运行完整流程"
            }
          >
            <Layers3 size={14} />
            严格完整流程
          </button>
        </div>
        <div className="data-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th className="selection-cell">
                  <input
                    type="checkbox"
                    aria-label="选择当前列表全部论文"
                    checked={
                      papers.length > 0 &&
                      papers.every((paper) =>
                        selectedPaperIds.has(paper.paper_id),
                      )
                    }
                    onChange={(event) =>
                      event.target.checked
                        ? selectVisible()
                        : setSelectedPaperIds(
                            new Set(
                              [...selectedPaperIds].filter(
                                (paperId) =>
                                  !papers.some(
                                    (paper) => paper.paper_id === paperId,
                                  ),
                              ),
                            ),
                          )
                    }
                  />
                </th>
                <th>论文</th>
                <th>Card</th>
                <th>结构</th>
                <th>单篇分析</th>
                <th>相关性</th>
                <th>Evidence</th>
                <th>问题</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {papers.map((paper) => (
                <PaperTableRow
                  key={paper.paper_id}
                  paper={paper}
                  projectId={project.project_id}
                  selected={selectedPaperIds.has(paper.paper_id)}
                  onSelect={(selected) =>
                    setSelectedPaperIds((current) => {
                      const next = new Set(current);
                      if (selected) next.add(paper.paper_id);
                      else next.delete(paper.paper_id);
                      return next;
                    })
                  }
                />
              ))}
            </tbody>
          </table>
        </div>
        {project.paper_count === 0 && (
          <div className="empty-project">
            <Files size={24} />
            <strong>任务中还没有论文</strong>
            <p>导入 PDF、Markdown 或文本来源后，系统会冻结第一版 collection。</p>
            <button
              type="button"
              className="primary-button"
              onClick={() => setImportOpen(true)}
            >
              <Upload size={15} />
              导入论文
            </button>
          </div>
        )}
      </section>
      {importOpen && (
        <ImportSourcesDialog
          project={project}
          onClose={() => setImportOpen(false)}
        />
      )}
      {editOpen && (
        <ProjectEditorDialog
          project={project}
          onClose={() => setEditOpen(false)}
        />
      )}
      {cardJobOpen && (
        <CardJobDialog
          project={project}
          papers={selectedPapers}
          onClose={() => setCardJobOpen(false)}
          onStarted={() => {
            setSelectedPaperIds(new Set());
            setCardJobOpen(false);
          }}
        />
      )}
      {analysisJobOpen && (
        <TopicBriefJobDialog
          project={project}
          papers={selectedPapers}
          onClose={() => setAnalysisJobOpen(false)}
          onStarted={() => {
            setSelectedPaperIds(new Set());
            setAnalysisJobOpen(false);
          }}
        />
      )}
      {synthesisJobOpen && (
        <TopicSynthesisJobDialog
          project={project}
          papers={synthesisSources}
          onClose={() => setSynthesisJobOpen(false)}
          onStarted={() => setSynthesisJobOpen(false)}
        />
      )}
      {fullPipelineOpen && (
        <FullPipelineJobDialog
          project={project}
          onClose={() => setFullPipelineOpen(false)}
          onStarted={() => setFullPipelineOpen(false)}
        />
      )}
    </>
  );
}

function CardJobDialog({
  project,
  papers,
  onClose,
  onStarted,
}: {
  project: Project;
  papers: Project["papers"];
  onClose: () => void;
  onStarted: (job: CardJob) => void;
}) {
  const queryClient = useQueryClient();
  const hasPdf = papers.some((paper) =>
    paper.source.toLowerCase().endsWith(".pdf"),
  );
  const pdfProvider = hasPdf ? "mineru" : "none";
  const [externalConfirmed, setExternalConfirmed] = useState(false);
  const request = {
    expected_revision: project.revision,
    paper_ids: papers.map((paper) => paper.paper_id),
    pdf_provider: pdfProvider as "none" | "mineru",
  };
  const preflight = useQuery({
    queryKey: [
      "card-job-preflight",
      project.project_id,
      project.revision,
      request.paper_ids,
      pdfProvider,
    ],
    queryFn: () => api.preflightCardJob(project.project_id, request),
    retry: false,
  });
  const mutation = useMutation({
    mutationFn: () => api.createCardJob(project.project_id, request),
    onSuccess: async (job) => {
      await queryClient.invalidateQueries({
        queryKey: ["jobs", project.project_id],
      });
      onStarted(job);
    },
  });
  const canStart =
    preflight.data &&
    !mutation.isPending &&
    (!preflight.data.uses_external_service || externalConfirmed);
  return (
    <div className="dialog-backdrop" role="presentation">
      <section
        className="dialog-panel dialog-wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="card-job-dialog-title"
      >
        <div className="dialog-header">
          <div>
            <span>M2.2 Card Job</span>
            <h2 id="card-job-dialog-title">确认论文制卡任务</h2>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            title="关闭"
          >
            <X size={17} />
          </button>
        </div>
        <div className="job-preflight-body">
          {preflight.isLoading && <LoadingBlock label="正在核验冻结输入" />}
          {preflight.error && <InlineError error={preflight.error} />}
          {preflight.data && (
            <>
              <div className="preflight-summary">
                <div>
                  <span>论文</span>
                  <strong>{preflight.data.papers.length} 篇</strong>
                </div>
                <div>
                  <span>项目 revision</span>
                  <strong>{preflight.data.project_revision}</strong>
                </div>
                <div>
                  <span>PDF 解析</span>
                  <strong>
                    {preflight.data.uses_external_service
                      ? "MinerU 精确模式"
                      : "不需要"}
                  </strong>
                </div>
              </div>
              <div className="frozen-input">
                <span>Collection SHA-256</span>
                <code>{preflight.data.collection_sha256}</code>
              </div>
              <div className="job-paper-list">
                {preflight.data.papers.map((paper) => (
                  <div key={paper.paper_id}>
                    <div>
                      <strong>{paper.paper_id}</strong>
                      <span>
                        {paper.source_suffix.toUpperCase()} ·{" "}
                        {formatBytes(paper.source_size_bytes)}
                      </span>
                    </div>
                    <code>{paper.source_sha256.slice(0, 23)}…</code>
                  </div>
                ))}
              </div>
              {preflight.data.uses_external_service && (
                <label className="external-confirmation">
                  <input
                    type="checkbox"
                    checked={externalConfirmed}
                    onChange={(event) =>
                      setExternalConfirmed(event.target.checked)
                    }
                  />
                  <span>
                    确认将所选 PDF 发送给已配置的 MinerU 精确解析服务。
                  </span>
                </label>
              )}
            </>
          )}
          {mutation.error && <InlineError error={mutation.error} />}
          <p className="form-note">
            Job 按论文顺序执行。单篇失败会记录并继续处理其他论文；只要存在成功论文，
            Job 就以“部分完成”发布成功结果。
          </p>
          <div className="dialog-actions">
            <button type="button" className="secondary-button" onClick={onClose}>
              取消
            </button>
            <button
              type="button"
              className="primary-button"
              disabled={!canStart}
              onClick={() => mutation.mutate()}
            >
              <Play size={14} />
              {mutation.isPending ? "正在创建" : "创建并运行"}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

function TopicBriefJobDialog({
  project,
  papers,
  onClose,
  onStarted,
}: {
  project: Project;
  papers: Project["papers"];
  onClose: () => void;
  onStarted: (job: CardJob) => void;
}) {
  const queryClient = useQueryClient();
  const [externalConfirmed, setExternalConfirmed] = useState(false);
  const baseRequest = {
    expected_revision: project.revision,
    paper_ids: papers.map((paper) => paper.paper_id),
    external_service_confirmed: false,
  };
  const preflight = useQuery({
    queryKey: [
      "topic-brief-job-preflight",
      project.project_id,
      project.revision,
      baseRequest.paper_ids,
    ],
    queryFn: () =>
      api.preflightTopicBriefJob(project.project_id, baseRequest),
    retry: false,
  });
  const mutation = useMutation({
    mutationFn: () =>
      api.createTopicBriefJob(project.project_id, {
        ...baseRequest,
        external_service_confirmed: true,
      }),
    onSuccess: async (job) => {
      await queryClient.invalidateQueries({
        queryKey: ["jobs", project.project_id],
      });
      onStarted(job);
    },
  });
  const totalCards =
    preflight.data?.papers.reduce(
      (sum, paper) => sum + paper.material_count,
      0,
    ) ?? 0;
  return (
    <div className="dialog-backdrop" role="presentation">
      <section
        className="dialog-panel dialog-wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="topic-brief-job-dialog-title"
      >
        <div className="dialog-header">
          <div>
            <span>M2.3 单篇主题分析</span>
            <h2 id="topic-brief-job-dialog-title">确认 Card 外发与分析</h2>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            title="关闭"
          >
            <X size={17} />
          </button>
        </div>
        <div className="job-preflight-body">
          {preflight.isLoading && <LoadingBlock label="正在冻结 Card 输入" />}
          {preflight.error && <InlineError error={preflight.error} />}
          {preflight.data && (
            <>
              <div className="preflight-summary">
                <div>
                  <span>论文</span>
                  <strong>{preflight.data.papers.length} 篇</strong>
                </div>
                <div>
                  <span>Card</span>
                  <strong>{totalCards} 张</strong>
                </div>
                <div>
                  <span>模型</span>
                  <strong>{preflight.data.model}</strong>
                </div>
                <div>
                  <span>推理模式</span>
                  <strong>
                    {preflight.data.thinking?.type === "disabled"
                      ? "Non-think"
                      : preflight.data.thinking?.type ?? "未声明"}
                  </strong>
                </div>
              </div>
              <div className="frozen-input">
                <span>模型配置 SHA-256</span>
                <code>{preflight.data.model_profile_sha256}</code>
              </div>
              <div className="job-paper-list">
                {preflight.data.papers.map((paper) => (
                  <div key={paper.paper_id}>
                    <div>
                      <strong>{paper.paper_id}</strong>
                      <span>
                        {paper.material_count} Card ·{" "}
                        {formatBytes(paper.materials_size_bytes)}
                      </span>
                      <span>generation {paper.generation_id}</span>
                    </div>
                    <code>{paper.input_sha256.slice(0, 31)}…</code>
                  </div>
                ))}
              </div>
              <label className="external-confirmation">
                <input
                  type="checkbox"
                  checked={externalConfirmed}
                  onChange={(event) =>
                    setExternalConfirmed(event.target.checked)
                  }
                />
                <span>
                  确认将上述 {totalCards} 张 Card 的分析内容发送给外部
                  DeepSeek 服务。论文之间保持隔离，按篇顺序调用。
                </span>
              </label>
            </>
          )}
          {mutation.error && <InlineError error={mutation.error} />}
          <p className="form-note">
            每篇论文使用冻结的 Card generation。执行前若 Card 内容或项目
            revision 变化，Job 会直接失败；不会沿用旧分析结果。
          </p>
          <div className="dialog-actions">
            <button type="button" className="secondary-button" onClick={onClose}>
              取消
            </button>
            <button
              type="button"
              className="primary-button"
              disabled={
                !preflight.data || !externalConfirmed || mutation.isPending
              }
              onClick={() => mutation.mutate()}
            >
              <Play size={14} />
              {mutation.isPending ? "正在创建" : "确认并运行"}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

function TopicSynthesisJobDialog({
  project,
  papers,
  onClose,
  onStarted,
}: {
  project: Project;
  papers: Project["papers"];
  onClose: () => void;
  onStarted: (job: CardJob) => void;
}) {
  const queryClient = useQueryClient();
  const [selectedRuns, setSelectedRuns] = useState(
    () =>
      new Set(
        papers
          .map((paper) => paper.analysis.run_id)
          .filter((value): value is string => Boolean(value)),
      ),
  );
  const [externalConfirmed, setExternalConfirmed] = useState(false);
  const sourceRunIds = papers
    .map((paper) => paper.analysis.run_id)
    .filter(
      (value): value is string =>
        Boolean(value) && selectedRuns.has(value as string),
    );
  const request = {
    expected_revision: project.revision,
    source_run_ids: sourceRunIds,
    external_service_confirmed: false,
  };
  const preflight = useQuery({
    queryKey: [
      "topic-synthesis-job-preflight",
      project.project_id,
      project.revision,
      sourceRunIds,
    ],
    queryFn: () =>
      api.preflightTopicSynthesisJob(project.project_id, request),
    enabled: sourceRunIds.length >= 2,
    retry: false,
  });
  const mutation = useMutation({
    mutationFn: () =>
      api.createTopicSynthesisJob(project.project_id, {
        ...request,
        external_service_confirmed: true,
      }),
    onSuccess: async (job) => {
      await queryClient.invalidateQueries({
        queryKey: ["jobs", project.project_id],
      });
      onStarted(job);
    },
  });
  return (
    <div className="dialog-backdrop" role="presentation">
      <section
        className="dialog-panel dialog-wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="topic-synthesis-job-dialog-title"
      >
        <div className="dialog-header">
          <div>
            <span>M2.4 跨论文综合</span>
            <h2 id="topic-synthesis-job-dialog-title">选择单篇分析 run</h2>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            title="关闭"
          >
            <X size={17} />
          </button>
        </div>
        <div className="job-preflight-body">
          <div className="job-paper-list">
            {papers.map((paper) => {
              const runId = paper.analysis.run_id as string;
              return (
                <label key={runId}>
                  <input
                    type="checkbox"
                    checked={selectedRuns.has(runId)}
                    onChange={(event) =>
                      setSelectedRuns((current) => {
                        const next = new Set(current);
                        if (event.target.checked) next.add(runId);
                        else next.delete(runId);
                        return next;
                      })
                    }
                  />
                  <div>
                    <strong>{paper.paper_title}</strong>
                    <span>
                      {statusLabels[paper.analysis.status]} ·{" "}
                      {paper.analysis.evidence_unit_count} Evidence
                    </span>
                  </div>
                  <code>{runId}</code>
                </label>
              );
            })}
          </div>
          {sourceRunIds.length < 2 && (
            <div className="inline-error">
              <AlertTriangle size={15} />
              <p>至少选择两个单篇分析 run。</p>
            </div>
          )}
          {preflight.isLoading && <LoadingBlock label="正在冻结综合输入" />}
          {preflight.error && <InlineError error={preflight.error} />}
          {preflight.data && (
            <>
              <div className="preflight-summary">
                <div>
                  <span>论文</span>
                  <strong>{preflight.data.source_paper_count} 篇</strong>
                </div>
                <div>
                  <span>Evidence</span>
                  <strong>{preflight.data.source_evidence_count} 条</strong>
                </div>
                <div>
                  <span>模型</span>
                  <strong>{preflight.data.model}</strong>
                </div>
                <div>
                  <span>推理模式</span>
                  <strong>
                    {preflight.data.thinking?.type === "disabled"
                      ? "Non-think"
                      : preflight.data.thinking?.type}
                  </strong>
                </div>
              </div>
              <div className="frozen-input">
                <span>Synthesis collection SHA-256</span>
                <code>{preflight.data.collection_sha256}</code>
              </div>
              <label className="external-confirmation">
                <input
                  type="checkbox"
                  checked={externalConfirmed}
                  onChange={(event) =>
                    setExternalConfirmed(event.target.checked)
                  }
                />
                <span>
                  确认将上述 {preflight.data.source_evidence_count} 条
                  Evidence 及单篇简报发送给外部 DeepSeek 服务。
                </span>
              </label>
            </>
          )}
          {mutation.error && <InlineError error={mutation.error} />}
          <p className="form-note">
            综合只读取这里显式选择的不可变 run。任何来源哈希变化都会终止 Job，
            不会自动改选其他 run。
          </p>
          <div className="dialog-actions">
            <button type="button" className="secondary-button" onClick={onClose}>
              取消
            </button>
            <button
              type="button"
              className="primary-button"
              disabled={
                !preflight.data || !externalConfirmed || mutation.isPending
              }
              onClick={() => mutation.mutate()}
            >
              <Play size={14} />
              {mutation.isPending ? "正在创建" : "确认并运行"}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

function FullPipelineJobDialog({
  project,
  onClose,
  onStarted,
}: {
  project: Project;
  onClose: () => void;
  onStarted: (job: CardJob) => void;
}) {
  const queryClient = useQueryClient();
  const [externalConfirmed, setExternalConfirmed] = useState(false);
  const preflight = useQuery({
    queryKey: ["full-pipeline-preflight", project.project_id, project.revision],
    queryFn: () =>
      api.preflightFullPipelineJob(project.project_id, {
        expected_revision: project.revision,
        external_service_confirmed: false,
      }),
  });
  const mutation = useMutation({
    mutationFn: () =>
      api.createFullPipelineJob(project.project_id, {
        expected_revision: project.revision,
        external_service_confirmed: externalConfirmed,
      }),
    onSuccess: (job) => {
      void queryClient.invalidateQueries({
        queryKey: ["jobs", project.project_id],
      });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      onStarted(job);
    },
  });
  const data = preflight.data;
  return (
    <div className="dialog-backdrop" role="presentation">
      <section className="dialog wide-dialog" role="dialog" aria-modal="true">
        <div className="dialog-header">
          <div>
            <span>M3.1 完整流程</span>
            <h2>从来源重新生成到跨论文综合</h2>
          </div>
          <button
            type="button"
            className="icon-button"
            title="关闭"
            aria-label="关闭"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </div>
        <div className="dialog-body">
          {preflight.isLoading && <LoadingBlock label="正在冻结完整流程输入" />}
          {preflight.error && <InlineError error={preflight.error} />}
          {data && (
            <>
              <div className="preflight-metrics">
                <div>
                  <span>论文</span>
                  <strong>{data.papers.length}</strong>
                </div>
                <div>
                  <span>模型</span>
                  <strong>{data.model}</strong>
                </div>
                <div>
                  <span>PDF</span>
                  <strong>{data.pdf_provider === "mineru" ? "MinerU" : "无"}</strong>
                </div>
                <div>
                  <span>推理</span>
                  <strong>{data.thinking.type === "disabled" ? "Non-think" : data.thinking.type}</strong>
                </div>
              </div>
              <div className="pipeline-stage-preview">
                <span>来源 / PDF</span>
                <ChevronRight size={14} />
                <span>Card</span>
                <ChevronRight size={14} />
                <span>单篇分析</span>
                <ChevronRight size={14} />
                <span>跨论文综合</span>
              </div>
              <div className="preflight-paper-list">
                {data.papers.map((paper) => (
                  <div key={paper.paper_id}>
                    <FileSearch size={14} />
                    <div>
                      <strong>{paper.paper_id}</strong>
                      <span>
                        {paper.source_suffix.toUpperCase()} ·{" "}
                        {formatBytes(paper.source_size_bytes)}
                      </span>
                    </div>
                    <code>{paper.source_sha256.slice(0, 18)}…</code>
                  </div>
                ))}
              </div>
              <div className="preflight-hash">
                <span>冻结 collection</span>
                <code>{data.collection_sha256}</code>
              </div>
              <label className="confirmation-check">
                <input
                  type="checkbox"
                  checked={externalConfirmed}
                  onChange={(event) =>
                    setExternalConfirmed(event.target.checked)
                  }
                />
                <span>
                  我确认本次将
                  {data.pdf_provider === "mineru" ? " PDF 发送给 MinerU，并将" : " "}
                  Card 与 Evidence 发送给 DeepSeek。任一阶段失败后停止后续阶段，
                  不自动重试或复用旧结果。
                </span>
              </label>
            </>
          )}
          {mutation.error && <InlineError error={mutation.error} />}
          <div className="dialog-actions">
            <button type="button" className="secondary-button" onClick={onClose}>
              取消
            </button>
            <button
              type="button"
              className="primary-button"
              disabled={!data || !externalConfirmed || mutation.isPending}
              onClick={() => mutation.mutate()}
            >
              <Play size={14} />
              {mutation.isPending ? "正在创建" : "确认并运行"}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

function JobPanel({ job }: { job: CardJob }) {
  const queryClient = useQueryClient();
  const cancelMutation = useMutation({
    mutationFn: () => api.cancelJob(job.job_id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["jobs", job.project_id],
      });
    },
  });
  const logQuery = useQuery({
    queryKey: ["job-log", job.job_id],
    queryFn: () => api.jobLog(job.job_id),
    refetchInterval:
      job.status === "queued" ||
      job.status === "running" ||
      job.status === "cancel_requested"
        ? 1000
        : false,
  });
  const progress =
    job.progress.total > 0
      ? Math.round((job.progress.completed / job.progress.total) * 100)
      : 0;
  return (
    <section className="card-job-panel">
      <div className="card-job-header">
        <div className="card-job-title">
          <RefreshCw
            size={17}
            className={
              job.status === "running" ||
              job.status === "cancel_requested"
                ? "spin"
                : ""
            }
          />
          <div>
            <span>
              {job.job_type === "card_build"
                ? "最近 Card Job"
                : job.job_type === "topic_brief"
                  ? "最近单篇分析 Job"
                  : job.job_type === "full_pipeline"
                    ? "最近完整流程 Job"
                  : "最近跨论文综合 Job"}
            </span>
            <strong>{job.job_id}</strong>
          </div>
        </div>
        <div className="job-header-actions">
          <StatusBadge
            status={job.status}
            label={
              job.status === "completed_with_failures"
                ? "部分完成"
                : undefined
            }
          />
          {(job.status === "queued" ||
            job.status === "running" ||
            job.status === "cancel_requested") && (
            <button
              type="button"
              className="secondary-button compact-button"
              disabled={
                job.status === "cancel_requested" ||
                cancelMutation.isPending
              }
              onClick={() => cancelMutation.mutate()}
              title="停止当前子进程并保留已完成论文结果"
            >
              <Square size={13} />
              {job.status === "cancel_requested"
                ? "正在取消"
                : "取消任务"}
            </button>
          )}
        </div>
      </div>
      <div className="job-progress-row">
        <div className="job-progress-track">
          <span style={{ width: `${progress}%` }} />
        </div>
        <strong>
          {job.progress.completed} / {job.progress.total}
        </strong>
        <span>
          成功 {job.progress.succeeded} · 失败 {job.progress.failed}
        </span>
      </div>
      {job.progress.current_paper_id && (
        <p className="job-current-paper">
          正在处理：{job.progress.current_paper_id}
        </p>
      )}
      {job.failure_message && (
        <div
          className={`job-failure ${
            job.status === "completed_with_failures" ||
            job.status === "interrupted"
              ? "job-warning"
              : ""
          }`}
        >
          <code>{job.failure_code}</code>
          <span>{job.failure_message}</span>
        </div>
      )}
      {cancelMutation.error && <InlineError error={cancelMutation.error} />}
      {job.paper_results.length > 0 && (
        <div className="job-result-strip">
          {job.paper_results.map((result) => (
            <div key={result.paper_id}>
              <StatusBadge status={result.status} />
              <strong>{result.paper_id}</strong>
              <span>
                {result.status !== "completed"
                  ? result.failure_code
                  : job.job_type === "card_build"
                    ? `${result.material_count ?? 0} Card · ${result.structure_quality ?? "—"}`
                    : `${statusLabels[result.analysis_status ?? "unknown"]} · ${result.evidence_unit_count ?? 0} Evidence · ${result.usage?.total_tokens ?? 0} Token`}
              </span>
            </div>
          ))}
        </div>
      )}
      {job.result?.status === "completed" && (
        <div className="job-result-strip">
          <div>
            <StatusBadge status="completed" />
            <strong>
              {job.result.pipeline_run_id ?? job.result.synthesis_run_id}
            </strong>
            {job.job_type === "full_pipeline" ? (
              <span>
                {job.result.included_paper_count ?? 0} 篇纳入 ·{" "}
                {job.result.excluded_paper_count ?? 0} 篇排除 ·{" "}
                {job.result.usage?.total_tokens ?? 0} Token
              </span>
            ) : (
              <span>
                {job.result.theme_count ?? 0} 主题 ·{" "}
                {job.result.synthesis_unit_count ?? 0} 综合单元 ·{" "}
                {job.result.section_count ?? 0} 章节 ·{" "}
                {job.result.usage?.total_tokens ?? 0} Token
              </span>
            )}
          </div>
        </div>
      )}
      <details className="job-log">
        <summary>运行日志</summary>
        {logQuery.error ? (
          <InlineError error={logQuery.error} />
        ) : (
          <pre>
            {(logQuery.data?.lines ?? []).join("\n") || "等待日志输出…"}
          </pre>
        )}
      </details>
    </section>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "warning";
}) {
  return (
    <div className={`metric ${tone ? `metric-${tone}` : ""}`}>
      <span>{label}</span>
      <strong>{value.toLocaleString("zh-CN")}</strong>
    </div>
  );
}

function PaperTableRow({
  paper,
  projectId,
  selected,
  onSelect,
}: {
  paper: Project["papers"][number];
  projectId: string;
  selected: boolean;
  onSelect: (selected: boolean) => void;
}) {
  const navigate = useNavigate();
  return (
    <tr
      className="clickable-row"
      onClick={() =>
        navigate(
          `/projects/${projectId}/papers/${encodeURIComponent(paper.paper_id)}`,
        )
      }
    >
      <td className="selection-cell">
        <input
          type="checkbox"
          aria-label={`选择论文：${paper.paper_title}`}
          checked={selected}
          onClick={(event) => event.stopPropagation()}
          onChange={(event) => onSelect(event.target.checked)}
        />
      </td>
      <td className="paper-cell">
        <strong>{paper.paper_title}</strong>
        <span>{paper.card.generation_id ?? "—"}</span>
      </td>
      <td>
        <strong>{paper.card.material_count}</strong>
        <span className="muted"> / {paper.card.block_count} Block</span>
      </td>
      <td>
        <span
          className={
            paper.card.structure_quality
              ? `quality quality-${paper.card.structure_quality}`
              : "quality"
          }
        >
          {paper.card.structure_quality ?? "—"}
        </span>
      </td>
      <td>
        <StatusBadge status={paper.analysis.status} />
      </td>
      <td>
        {relevanceLabels[paper.analysis.paper_relevance ?? ""] ?? "未判定"}
      </td>
      <td>
        <strong>{paper.analysis.evidence_unit_count}</strong>
      </td>
      <td>
        <span
          className={
            paper.analysis.evidence_failure_count + paper.card.issue_count > 0
              ? "count-warning"
              : ""
          }
        >
          {paper.analysis.evidence_failure_count + paper.card.issue_count}
        </span>
      </td>
      <td>
        <ChevronRight size={16} />
      </td>
    </tr>
  );
}

type DetailTab = "cards" | "evidence" | "failures" | "structure";

function PaperPage() {
  const { projectId = PROJECT_ID, paperId = "" } = useParams();
  const decodedPaperId = decodeURIComponent(paperId);
  const [tab, setTab] = useState<DetailTab>("evidence");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const query = useQuery({
    queryKey: ["paper", projectId, decodedPaperId],
    queryFn: () => api.paper(projectId, decodedPaperId),
  });
  const detail = query.data as PaperDetail | undefined;
  const selected = detail
    ? selectDetailItem(detail, tab, selectedIndex)
    : null;
  const span = detailSpan(tab, selected);

  useEffect(() => {
    if (!span) return;
    document
      .getElementById(`source-line-${span.start_line}`)
      ?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [span?.start_line, tab, selectedIndex]);

  if (query.isLoading) return <LoadingBlock label="正在读取论文产物" />;
  if (query.error) return <ErrorBlock error={query.error} />;
  if (!detail) return <ErrorBlock error={new Error("论文产物为空。")} />;

  const chooseTab = (nextTab: DetailTab) => {
    setTab(nextTab);
    setSelectedIndex(0);
  };

  return (
    <div className="paper-page">
      <PageHeader
        eyebrow={`${detail.summary.card.material_count} Card · ${detail.summary.analysis.evidence_unit_count} Evidence`}
        title={detail.summary.paper_title}
        description={
          detail.summary.card.generation_id
            ? `generation ${detail.summary.card.generation_id}`
            : "尚未生成 Card"
        }
        backTo={`/projects/${projectId}`}
      />
      <div className="paper-status-bar">
        <StatusBadge status={detail.summary.analysis.status} />
        <span>
          结构质量 {detail.summary.card.structure_quality ?? "未评估"}
        </span>
        <span>
          三层覆盖 {coverageRatio(detail.coverage, "source_to_region_ratio")} /
          {" "}
          {coverageRatio(detail.coverage, "region_to_block_ratio")} /
          {" "}
          {coverageRatio(detail.coverage, "block_to_card_ratio")}
        </span>
        {span && (
          <span className="source-location">
            {span.path ?? "normalized/document.md"} L{span.start_line}-L
            {span.end_line}
          </span>
        )}
      </div>
      <div className="paper-split">
        <DocumentPane
          lines={detail.document.lines}
          activeSpan={span}
          lineCount={detail.document.line_count}
        />
        <PaperInspector
          detail={detail}
          tab={tab}
          selectedIndex={selectedIndex}
          onTab={chooseTab}
          onSelect={setSelectedIndex}
        />
      </div>
    </div>
  );
}

function DocumentPane({
  lines,
  activeSpan,
  lineCount,
}: {
  lines: string[];
  activeSpan: SourceSpan | null;
  lineCount: number;
}) {
  return (
    <section className="document-pane">
      <div className="pane-header">
        <div>
          <FileSearch size={17} />
          <strong>原始 Markdown</strong>
        </div>
        <span>{lineCount.toLocaleString("zh-CN")} 行</span>
      </div>
      <div className="source-lines">
        {lines.map((line, index) => {
          const lineNumber = index + 1;
          const active =
            activeSpan &&
            lineNumber >= activeSpan.start_line &&
            lineNumber <= activeSpan.end_line;
          return (
            <div
              id={`source-line-${lineNumber}`}
              key={lineNumber}
              className={`source-line ${active ? "source-line-active" : ""}`}
            >
              <span className="line-number">{lineNumber}</span>
              <span className="line-text">{line || " "}</span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function PaperInspector({
  detail,
  tab,
  selectedIndex,
  onTab,
  onSelect,
}: {
  detail: PaperDetail;
  tab: DetailTab;
  selectedIndex: number;
  onTab: (tab: DetailTab) => void;
  onSelect: (index: number) => void;
}) {
  const tabs: Array<[DetailTab, string, number]> = [
    ["cards", "Card", detail.cards.length],
    ["evidence", "Evidence", detail.evidence.length],
    ["failures", "失败", detail.failures.length],
    ["structure", "结构", detail.issues.length],
  ];
  const items =
    tab === "cards"
      ? detail.cards
      : tab === "evidence"
        ? detail.evidence
        : tab === "failures"
          ? detail.failures
          : detail.issues;
  const selected = items[selectedIndex];
  return (
    <section className="inspector">
      <div className="tabs">
        {tabs.map(([id, label, count]) => (
          <button
            type="button"
            key={id}
            className={tab === id ? "active" : ""}
            onClick={() => onTab(id)}
          >
            {label}
            <span>{count}</span>
          </button>
        ))}
      </div>
      {tab === "structure" ? (
        <StructureView detail={detail} />
      ) : items.length === 0 ? (
        <div className="empty-state">当前没有{tabs.find(([id]) => id === tab)?.[1]}记录。</div>
      ) : (
        <div className="inspector-body">
          <div className="item-list">
            {items.map((item, index) => (
              <button
                type="button"
                className={index === selectedIndex ? "active" : ""}
                key={detailItemKey(tab, item, index)}
                onClick={() => onSelect(index)}
              >
                <span>{index + 1}</span>
                <div>
                  <strong>{detailItemTitle(tab, item)}</strong>
                  <small>{detailItemSubtitle(tab, item)}</small>
                </div>
              </button>
            ))}
          </div>
          <div className="item-detail">
            {tab === "cards" && <CardView card={selected as Card} />}
            {tab === "evidence" && (
              <EvidenceView evidence={selected as Evidence} />
            )}
            {tab === "failures" && (
              <FailureView failure={selected as EvidenceFailure} />
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function CardView({ card }: { card: Card }) {
  return (
    <>
      <DetailHeading
        label={card.content_kind}
        title={card.clean_title}
        meta={`L${card.source_span.start_line}-L${card.source_span.end_line}`}
      />
      <DetailSection title="Card 内容">
        <p className="extract-text">{card.extract}</p>
      </DetailSection>
      <FlagList
        title="质量标记"
        flags={[...card.confidence_flags, ...card.quality_flags]}
      />
      <DetailSection title="章节路径">
        <p>{card.heading_path.join(" › ") || "无章节路径"}</p>
      </DetailSection>
    </>
  );
}

function EvidenceView({ evidence }: { evidence: Evidence }) {
  return (
    <>
      <DetailHeading
        label={evidence.evidence_type}
        title={evidence.claim}
        meta={`置信度 ${evidence.confidence}`}
      />
      <DetailSection title="主题相关性">
        <p>{evidence.relevance}</p>
      </DetailSection>
      {evidence.citations.map((citation, index) => (
        <DetailSection
          title={`逐字引文 ${index + 1}`}
          key={`${citation.material_id}-${index}`}
        >
          <blockquote>{citation.quote}</blockquote>
          <p className="detail-meta">
            {citation.card_title} · L{citation.source_ref.start_line}-L
            {citation.source_ref.end_line}
          </p>
        </DetailSection>
      ))}
      {evidence.caveats.length > 0 && (
        <DetailSection title="使用限制">
          <ul>
            {evidence.caveats.map((caveat) => (
              <li key={caveat}>{caveat}</li>
            ))}
          </ul>
        </DetailSection>
      )}
    </>
  );
}

function FailureView({ failure }: { failure: EvidenceFailure }) {
  return (
    <>
      <DetailHeading
        label="合同失败"
        title={failure.card_title}
        meta={`L${failure.source_span.start_line}-L${failure.source_span.end_line}`}
        tone="danger"
      />
      <DetailSection title="错误码">
        <code>{failure.error_code}</code>
      </DetailSection>
      <DetailSection title="错误说明">
        <p>{failure.error_message}</p>
      </DetailSection>
      <DetailSection title="失败 Card 原文">
        <p className="extract-text">
          {failure.material?.extract ?? "失败账本中没有对应的冻结 Card。"}
        </p>
      </DetailSection>
    </>
  );
}

function StructureView({ detail }: { detail: PaperDetail }) {
  const coverageEntries = [
    ["原文行 → region", coverageRatio(detail.coverage, "source_to_region_ratio")],
    ["纳入行 → block", coverageRatio(detail.coverage, "region_to_block_ratio")],
    ["block → Card", coverageRatio(detail.coverage, "block_to_card_ratio")],
  ];
  return (
    <div className="structure-view">
      <div className="coverage-grid">
        {coverageEntries.map(([label, value]) => (
          <div key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      <h3>结构问题</h3>
      {detail.issues.length === 0 ? (
        <p className="muted">没有结构问题。</p>
      ) : (
        <div className="issue-list">
          {detail.issues.map((issue) => (
            <div key={issue.code}>
              <AlertTriangle size={16} />
              <div>
                <code>{issue.code}</code>
                <p>{issue.message}</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DetailHeading({
  label,
  title,
  meta,
  tone,
}: {
  label: string;
  title: string;
  meta: string;
  tone?: "danger";
}) {
  return (
    <div className={`detail-heading ${tone ? `detail-${tone}` : ""}`}>
      <div>
        <span>{label}</span>
        <small>{meta}</small>
      </div>
      <h2>{title}</h2>
    </div>
  );
}

function DetailSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="detail-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function FlagList({ title, flags }: { title: string; flags: string[] }) {
  if (flags.length === 0) return null;
  return (
    <DetailSection title={title}>
      <div className="flag-list">
        {flags.map((flag) => (
          <code key={flag}>{flag}</code>
        ))}
      </div>
    </DetailSection>
  );
}

function selectDetailItem(
  detail: PaperDetail,
  tab: DetailTab,
  selectedIndex: number,
) {
  const items =
    tab === "cards"
      ? detail.cards
      : tab === "evidence"
        ? detail.evidence
        : tab === "failures"
          ? detail.failures
          : detail.issues;
  return items[selectedIndex] ?? null;
}

function detailSpan(tab: DetailTab, item: unknown): SourceSpan | null {
  if (!item || tab === "structure") return null;
  if (tab === "cards") return (item as Card).source_span;
  if (tab === "failures") return (item as EvidenceFailure).source_span;
  return (item as Evidence).citations[0]?.source_ref ?? null;
}

function detailItemKey(tab: DetailTab, item: unknown, index: number) {
  if (tab === "cards") return (item as Card).material_id;
  if (tab === "evidence") return (item as Evidence).evidence_unit_id;
  if (tab === "failures")
    return `${(item as EvidenceFailure).material_id}-${index}`;
  return index;
}

function detailItemTitle(tab: DetailTab, item: unknown) {
  if (tab === "cards") return (item as Card).clean_title;
  if (tab === "evidence") return (item as Evidence).claim;
  if (tab === "failures") return (item as EvidenceFailure).card_title;
  return "";
}

function detailItemSubtitle(tab: DetailTab, item: unknown) {
  if (tab === "cards") {
    const card = item as Card;
    return `${card.content_kind} · L${card.source_span.start_line}-L${card.source_span.end_line}`;
  }
  if (tab === "evidence") {
    const evidence = item as Evidence;
    return `${evidence.evidence_type} · ${evidence.confidence}`;
  }
  if (tab === "failures") return (item as EvidenceFailure).error_code;
  return "";
}

type SynthesisTab = "themes" | "units" | "outline" | "audit";

function SynthesisPage() {
  const { projectId = PROJECT_ID } = useParams();
  const [tab, setTab] = useState<SynthesisTab>("themes");
  const query = useQuery({
    queryKey: ["synthesis", projectId],
    queryFn: () => api.synthesis(projectId),
  });
  if (query.isLoading) return <LoadingBlock label="正在读取综合产物" />;
  if (query.error) return <ErrorBlock error={query.error} />;
  const synthesis = query.data as SynthesisDetail;
  return (
    <>
      <PageHeader
        eyebrow="跨论文综合"
        title="主题矩阵与综述提纲"
        description={synthesis.output.topic}
        backTo={`/projects/${projectId}`}
      />
      <div className="synthesis-status">
        <StatusBadge status={String(synthesis.manifest.status)} />
        <span>{synthesis.output.source_runs.length} 篇论文</span>
        <span>
          {Number(synthesis.output.coverage.source_evidence_count)} 条 Evidence
        </span>
        <span>run {synthesis.run_id}</span>
      </div>
      <div className="wide-tabs">
        {[
          ["themes", "主题矩阵", synthesis.output.evidence_map.themes.length],
          [
            "units",
            "综合单元",
            synthesis.output.outline.synthesis_units.length,
          ],
          ["outline", "章节提纲", synthesis.output.outline.sections.length],
          ["audit", "覆盖审计", null],
        ].map(([id, label, count]) => (
          <button
            type="button"
            key={String(id)}
            className={tab === id ? "active" : ""}
            onClick={() => setTab(id as SynthesisTab)}
          >
            {label}
            {typeof count === "number" && <span>{count}</span>}
          </button>
        ))}
      </div>
      <section className="synthesis-content">
        {tab === "themes" && <ThemesView synthesis={synthesis} />}
        {tab === "units" && <UnitsView synthesis={synthesis} />}
        {tab === "outline" && <OutlineView synthesis={synthesis} />}
        {tab === "audit" && <AuditView synthesis={synthesis} />}
      </section>
    </>
  );
}

function ThemesView({ synthesis }: { synthesis: SynthesisDetail }) {
  return (
    <div className="theme-list">
      {synthesis.output.evidence_map.themes.map((theme, index) => (
        <section key={theme.theme_id} className="theme-row">
          <div className="theme-number">{String(index + 1).padStart(2, "0")}</div>
          <div className="theme-main">
            <div className="theme-title-row">
              <h2>{theme.title}</h2>
              <span className="relation-badge">
                {relationLabels[theme.relation_type] ?? theme.relation_type}
              </span>
            </div>
            <p>{theme.focus}</p>
            <div className="synthesis-value">{theme.synthesis_value}</div>
            <div className="evidence-stack">
              {theme.assignments.map((assignment) => {
                const evidence =
                  synthesis.evidence_catalog[assignment.evidence_unit_id];
                return (
                  <EvidenceCompact
                    key={assignment.evidence_unit_id}
                    evidence={evidence}
                    role={assignment.role}
                  />
                );
              })}
            </div>
          </div>
        </section>
      ))}
    </div>
  );
}

function EvidenceCompact({
  evidence,
  role,
}: {
  evidence?: Evidence;
  role?: string;
}) {
  if (!evidence) return <div className="missing-evidence">Evidence 产物缺失</div>;
  return (
    <details className="evidence-compact">
      <summary>
        <span className="paper-dot" />
        <strong>{evidence.paper_title}</strong>
        <p>{evidence.claim}</p>
        {role && <small>{role}</small>}
      </summary>
      <div>
        <p>{evidence.relevance}</p>
        {evidence.citations.map((citation, index) => (
          <blockquote key={`${citation.material_id}-${index}`}>
            {citation.quote}
            <cite>
              {citation.card_title} · L{citation.source_ref.start_line}-L
              {citation.source_ref.end_line}
            </cite>
          </blockquote>
        ))}
      </div>
    </details>
  );
}

function UnitsView({ synthesis }: { synthesis: SynthesisDetail }) {
  const themes = Object.fromEntries(
    synthesis.output.evidence_map.themes.map((theme) => [
      theme.theme_id,
      theme.title,
    ]),
  );
  return (
    <div className="unit-list">
      {synthesis.output.outline.synthesis_units.map((unit, index) => (
        <section key={unit.synthesis_unit_id} className="unit-row">
          <div className="unit-index">{index + 1}</div>
          <div>
            <div className="unit-meta">
              <span className="relation-badge">
                {relationLabels[unit.unit_type] ?? unit.unit_type}
              </span>
              <span>{unit.evidence_unit_ids.length} 条 Evidence</span>
            </div>
            <h2>{unit.synthesis_statement}</h2>
            <p className="theme-reference">
              {unit.theme_ids.map((id) => themes[id] ?? id).join(" · ")}
            </p>
            <div className="evidence-stack">
              {unit.evidence_unit_ids.map((id) => (
                <EvidenceCompact
                  key={id}
                  evidence={synthesis.evidence_catalog[id]}
                />
              ))}
            </div>
          </div>
        </section>
      ))}
    </div>
  );
}

function OutlineView({ synthesis }: { synthesis: SynthesisDetail }) {
  return (
    <div className="outline-list">
      {synthesis.output.outline.sections.map((section, sectionIndex) => (
        <section key={`${section.title}-${sectionIndex}`} className="outline-section">
          <div className="outline-heading">
            <span>{sectionIndex + 1}</span>
            <div>
              <h2>{section.title}</h2>
              <p>{section.purpose}</p>
            </div>
          </div>
          <div className="paragraph-list">
            {section.paragraphs.map((paragraph, paragraphIndex) => (
              <div key={paragraphIndex} className="paragraph-row">
                <span>{sectionIndex + 1}.{paragraphIndex + 1}</span>
                <div>
                  <strong>{paragraph.synthesis_move}</strong>
                  <small>
                    {paragraph.paragraph_role} ·{" "}
                    {paragraph.evidence_unit_ids.length} Evidence ·{" "}
                    {paragraph.synthesis_unit_ids.length} 综合单元
                  </small>
                </div>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function AuditView({ synthesis }: { synthesis: SynthesisDetail }) {
  const coverage = synthesis.output.coverage;
  const rows: Array<[string, string]> = [
    ["来源 Evidence", "source_evidence_count"],
    ["主题归属记录", "theme_assignment_count"],
    ["仅归属一个主题", "single_theme_evidence_count"],
    ["归属多个主题", "multi_theme_evidence_count"],
    ["主题未归组", "theme_unassigned_count"],
    ["进入提纲", "outline_used_count"],
    ["已归组但未进入提纲", "assigned_but_unused_count"],
    ["综合单元", "synthesis_unit_count"],
    ["跨论文综合单元", "cross_paper_synthesis_unit_count"],
    ["部分状态论文", "partial_source_run_ids"],
  ];
  const unused = (coverage.assigned_but_unused_evidence_ids ?? []) as string[];
  return (
    <div className="audit-layout">
      <section className="audit-table">
        <h2>覆盖统计</h2>
        {rows.map(([label, key]) => {
          const value = coverage[key];
          return (
            <div key={key}>
              <span>{label}</span>
              <strong>{Array.isArray(value) ? value.length : String(value ?? 0)}</strong>
            </div>
          );
        })}
      </section>
      <section className="audit-details">
        <h2>未归组 Evidence</h2>
        {synthesis.output.evidence_map.unassigned_evidence.map((row) => (
          <div className="audit-item" key={row.evidence_unit_id}>
            <EvidenceCompact
              evidence={synthesis.evidence_catalog[row.evidence_unit_id]}
            />
            <p>
              <code>{row.reason_code}</code> {row.reason}
            </p>
          </div>
        ))}
        <h2>已归组但未进入提纲</h2>
        <div className="evidence-stack">
          {unused.map((id) => (
            <EvidenceCompact
              key={id}
              evidence={synthesis.evidence_catalog[id]}
            />
          ))}
        </div>
      </section>
    </div>
  );
}

function RunsPage() {
  const [type, setType] = useState("all");
  const [selectedRunIds, setSelectedRunIds] = useState<string[]>([]);
  const query = useQuery({
    queryKey: ["runs"],
    queryFn: () => api.runs(),
  });
  if (query.isLoading) return <LoadingBlock label="正在汇总运行记录" />;
  if (query.error) return <ErrorBlock error={query.error} />;
  const runs = (query.data ?? []).filter(
    (run) => type === "all" || run.run_type === type,
  );
  const selectedRuns = selectedRunIds
    .map((id) => (query.data ?? []).find((run) => run.run_id === id))
    .filter((run): run is RunRecord => Boolean(run));
  const comparisonAnchor = selectedRuns[0];
  const chooseRun = (run: RunRecord) => {
    setSelectedRunIds((current) => {
      if (current.includes(run.run_id)) {
        return current.filter((id) => id !== run.run_id);
      }
      if (current.length === 0) return [run.run_id];
      if (
        comparisonAnchor &&
        (comparisonAnchor.project_id !== run.project_id ||
          comparisonAnchor.run_type !== run.run_type)
      ) {
        return current;
      }
      if (current.length === 1) return [...current, run.run_id];
      return [current[1], run.run_id];
    });
  };
  return (
    <>
      <PageHeader
        eyebrow="不可变运行"
        title="运行记录"
        description="查看全部项目的 Card、单篇分析、综合和完整流程运行。"
      />
      <section className="content-section">
        <div className="toolbar">
          <label className="filter-field">
            <Filter size={15} />
            <select value={type} onChange={(event) => setType(event.target.value)}>
              <option value="all">全部阶段</option>
              <option value="card_build">Card 制作</option>
              <option value="topic_brief">单篇分析 Job</option>
              <option value="topic_synthesis">跨论文综合 Job</option>
              <option value="full_pipeline">完整流程 Job</option>
              <option value="pipeline">历史完整流程</option>
              <option value="topic_review">单篇分析</option>
              <option value="topic_synthesis">跨论文综合</option>
            </select>
          </label>
          <div className="run-compare-hint">
            <GitCompareArrows size={15} />
            <span>
              {selectedRunIds.length === 0
                ? "选择两个同项目、同阶段 Job"
                : `已选择 ${selectedRunIds.length} / 2`}
            </span>
            {selectedRunIds.length > 0 && (
              <button
                type="button"
                className="icon-button"
                title="清除比较选择"
                aria-label="清除比较选择"
                onClick={() => setSelectedRunIds([])}
              >
                <X size={14} />
              </button>
            )}
          </div>
          <span className="toolbar-count">{runs.length} 次运行</span>
        </div>
        {selectedRuns.length === 2 &&
          selectedRuns[0].project_id &&
          selectedRuns[1].project_id && (
            <RunComparisonPanel
              projectId={selectedRuns[0].project_id}
              leftJobId={selectedRuns[0].run_id}
              rightJobId={selectedRuns[1].run_id}
            />
          )}
        <div className="run-list">
          {runs.map((run) => {
            const isJob =
              run.schema_version === "review_ui_job.v1" && Boolean(run.project_id);
            const compatible =
              !comparisonAnchor ||
              (comparisonAnchor.project_id === run.project_id &&
                comparisonAnchor.run_type === run.run_type);
            return (
              <RunRow
                key={`${run.run_type}-${run.run_id}`}
                run={run}
                selected={selectedRunIds.includes(run.run_id)}
                selectable={isJob && compatible}
                onSelect={() => chooseRun(run)}
              />
            );
          })}
        </div>
      </section>
    </>
  );
}

function RunComparisonPanel({
  projectId,
  leftJobId,
  rightJobId,
}: {
  projectId: string;
  leftJobId: string;
  rightJobId: string;
}) {
  const query = useQuery({
    queryKey: ["run-comparison", projectId, leftJobId, rightJobId],
    queryFn: () => api.runComparison(projectId, leftJobId, rightJobId),
  });
  if (query.isLoading) {
    return <LoadingBlock label="正在核对冻结输入" />;
  }
  if (query.error) return <InlineError error={query.error} />;
  if (!query.data) return null;
  const comparison = query.data;
  return (
    <section
      className={`run-comparison ${
        comparison.comparable ? "comparison-valid" : "comparison-blocked"
      }`}
    >
      <div className="comparison-header">
        <div>
          {comparison.comparable ? (
            <CheckCircle2 size={18} />
          ) : (
            <ShieldAlert size={18} />
          )}
          <div>
            <strong>
              {comparison.comparable
                ? "冻结输入一致，可以比较"
                : "输入代际不同，已阻断差值"}
            </strong>
            <span>{comparison.job_type}</span>
          </div>
        </div>
        <span>
          配置变化 {comparison.configuration_differences.length} 项
        </span>
      </div>
      <div className="comparison-columns">
        <ComparisonMetrics
          label="基准"
          jobId={comparison.left.job_id}
          metrics={comparison.left.metrics}
        />
        <ComparisonMetrics
          label="对照"
          jobId={comparison.right.job_id}
          metrics={comparison.right.metrics}
        />
        {comparison.deltas && (
          <ComparisonDelta comparison={comparison} />
        )}
      </div>
      {comparison.input_differences.length > 0 && (
        <div className="comparison-differences">
          {comparison.input_differences.map((difference, index) => (
            <div key={`${difference.code}-${difference.paper_id}-${index}`}>
              <code>{difference.code}</code>
              <strong>{difference.message}</strong>
            </div>
          ))}
        </div>
      )}
      {comparison.configuration_differences.length > 0 && (
        <details className="comparison-config">
          <summary>查看执行配置变化</summary>
          {comparison.configuration_differences.map((difference, index) => (
            <div key={`${difference.field}-${index}`}>
              <code>{difference.field}</code>
              <span>{String(difference.left ?? "—")}</span>
              <ChevronRight size={13} />
              <span>{String(difference.right ?? "—")}</span>
            </div>
          ))}
        </details>
      )}
    </section>
  );
}

function ComparisonMetrics({
  label,
  jobId,
  metrics,
}: {
  label: string;
  jobId: string;
  metrics: RunComparison["left"]["metrics"];
}) {
  return (
    <div className="comparison-metrics">
      <span>{label}</span>
      <strong>{jobId}</strong>
      <StatusBadge status={metrics.status} />
      <dl>
        <div>
          <dt>Token</dt>
          <dd>{metrics.total_tokens.toLocaleString("zh-CN")}</dd>
        </div>
        <div>
          <dt>请求</dt>
          <dd>{metrics.request_count}</dd>
        </div>
        <div>
          <dt>耗时</dt>
          <dd>{formatDuration(metrics.duration_seconds)}</dd>
        </div>
        <div>
          <dt>失败</dt>
          <dd>{metrics.failure_count}</dd>
        </div>
      </dl>
    </div>
  );
}

function ComparisonDelta({ comparison }: { comparison: RunComparison }) {
  const deltas = comparison.deltas!;
  return (
    <div className="comparison-delta">
      <span>对照 - 基准</span>
      <strong>{formatSigned(deltas.total_tokens)} Token</strong>
      <dl>
        <div>
          <dt>请求</dt>
          <dd>{formatSigned(deltas.request_count)}</dd>
        </div>
        <div>
          <dt>耗时</dt>
          <dd>{formatSigned(deltas.duration_seconds, " 秒")}</dd>
        </div>
        <div>
          <dt>失败</dt>
          <dd>{formatSigned(deltas.failure_count)}</dd>
        </div>
        {Object.entries(deltas.output).map(([key, value]) => (
          <div key={key}>
            <dt>{outputMetricLabel(key)}</dt>
            <dd>{formatSigned(value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function RunRow({
  run,
  selected,
  selectable,
  onSelect,
}: {
  run: RunRecord;
  selected: boolean;
  selectable: boolean;
  onSelect: () => void;
}) {
  const typeLabel =
    run.run_type === "card_build"
      ? "Card 制作"
      : run.run_type === "topic_brief"
        ? "单篇分析 Job"
      : run.run_type === "topic_synthesis"
        ? run.schema_version === "review_ui_job.v1"
          ? "跨论文综合 Job"
          : "跨论文综合"
      : run.run_type === "pipeline"
      ? "完整流程"
      : run.run_type === "full_pipeline"
        ? "完整流程 Job"
      : run.run_type === "topic_review"
        ? "单篇分析"
        : "跨论文综合";
  return (
    <details className="run-row">
      <summary>
        <button
          type="button"
          className={`run-select ${selected ? "selected" : ""}`}
          title={
            selectable
              ? selected
                ? "移出运行比较"
                : "加入运行比较"
              : "只有同项目、同阶段的 UI Job 可以比较"
          }
          aria-label={selected ? "移出运行比较" : "加入运行比较"}
          aria-pressed={selected}
          disabled={!selectable && !selected}
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            onSelect();
          }}
        >
          <GitCompareArrows size={14} />
        </button>
        <span className="run-type">{typeLabel}</span>
        <div className="run-name">
          <strong>{run.paper_id ?? run.run_id}</strong>
          <span>{run.run_id}</span>
        </div>
        <StatusBadge status={run.status} />
        <div className="run-stat">
          <span>请求</span>
          <strong>{run.request_count}</strong>
        </div>
        <div className="run-stat">
          <span>Token</span>
          <strong>{run.total_tokens.toLocaleString("zh-CN")}</strong>
        </div>
        <div className="run-date">
          <Clock3 size={14} />
          {formatDate(run.started_at)}
        </div>
        <ChevronRight size={17} />
      </summary>
      <div className="run-detail">
        <div>
          <span>Schema</span>
          <code>{run.schema_version}</code>
        </div>
        <div>
          <span>开始时间</span>
          <strong>{formatDate(run.started_at, true)}</strong>
        </div>
        <div>
          <span>结束时间</span>
          <strong>{formatDate(run.finished_at, true)}</strong>
        </div>
        <div>
          <span>失败数</span>
          <strong>{run.failure_count}</strong>
        </div>
        {run.failure_codes.length > 0 && (
          <div className="run-failures">
            <span>失败代码</span>
            {run.failure_codes.map((code) => (
              <code key={code}>{code}</code>
            ))}
          </div>
        )}
      </div>
    </details>
  );
}

function SystemPage() {
  const query = useQuery({
    queryKey: ["system-status"],
    queryFn: api.systemStatus,
  });
  if (query.isLoading) return <LoadingBlock />;
  if (query.error) return <ErrorBlock error={query.error} />;
  const status = query.data!;
  return (
    <>
      <PageHeader
        eyebrow="本地服务"
        title="系统状态"
        description="M3.1 已开放四类受控 Job；外部服务仅在明确确认后调用。"
      />
      <section className="system-panel">
        <div>
          <Database size={19} />
          <span>工作区</span>
          <strong>{status.workspace}</strong>
          <StatusBadge
            status={status.workspace_available ? "completed" : "failed"}
          />
        </div>
        <div>
          <GitBranch size={19} />
          <span>运行模式</span>
          <strong>受控 Pipeline Job</strong>
          <StatusBadge status="completed" />
        </div>
        <div>
          <Boxes size={19} />
          <span>显式任务</span>
          <strong>{status.project_count} 个</strong>
          <StatusBadge status="completed" />
        </div>
        <div>
          <Activity size={19} />
          <span>Job 账本</span>
          <strong>
            {status.job_count} 个账本 · {status.active_job_count} 个活动
          </strong>
          <StatusBadge
            status={status.active_job_count > 0 ? "running" : "completed"}
          />
        </div>
        <div>
          <Layers3 size={19} />
          <span>外部服务</span>
          <strong>PDF 按任务调用 MinerU</strong>
          <span className="status-badge status-neutral">显式确认</span>
        </div>
      </section>
    </>
  );
}

function coverageRatio(coverage: Record<string, unknown>, key: string) {
  const value = Number(coverage[key] ?? 0);
  return value.toFixed(1);
}

function formatDate(value?: string, includeTime = false) {
  if (!value) return "未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    ...(includeTime
      ? { hour: "2-digit", minute: "2-digit", second: "2-digit" }
      : { hour: "2-digit", minute: "2-digit" }),
  }).format(date);
}

function formatDuration(value: number | null) {
  if (value === null) return "未记录";
  return `${value.toFixed(value < 10 ? 1 : 0)} 秒`;
}

function formatSigned(value: number | null, suffix = "") {
  if (value === null) return "未记录";
  const formatted = Number.isInteger(value) ? String(value) : value.toFixed(1);
  return `${value > 0 ? "+" : ""}${formatted}${suffix}`;
}

function outputMetricLabel(key: string) {
  const labels: Record<string, string> = {
    material_count: "Card",
    issue_count: "结构问题",
    evidence_unit_count: "Evidence",
    evidence_failure_count: "Evidence 失败",
    theme_count: "主题",
    synthesis_unit_count: "综合单元",
    section_count: "章节",
    paper_count: "论文",
    included_paper_count: "纳入论文",
    excluded_paper_count: "排除论文",
    completed_card_count: "完成 Card",
  };
  return labels[key] ?? key;
}

function defaultProjectId() {
  const now = new Date();
  const stamp = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("");
  return `review-${stamp}`;
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export default function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<ProjectsPage />} />
        <Route path="/projects/:projectId" element={<ProjectPage />} />
        <Route
          path="/projects/:projectId/papers/:paperId"
          element={<PaperPage />}
        />
        <Route
          path="/projects/:projectId/synthesis"
          element={<SynthesisPage />}
        />
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/system" element={<SystemPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
