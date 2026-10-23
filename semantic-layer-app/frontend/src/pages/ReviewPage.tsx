import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import ChatPanel from "../components/ChatPanel";
import {
  Alert,
  Badge,
  Button,
  Card,
  Field,
  Input,
  Modal,
  PageHeader,
  Select,
  Spinner,
  StatusBadge,
  Tabs,
  Textarea,
  Toggle,
  cx,
} from "../components/ui";
import { api, errorMessage, formatNumber } from "../lib/api";
import {
  COLUMN_FIELDS,
  METRIC_FIELDS,
  attentionRank,
  changedFields,
  diffModels,
  issuesFor,
  needsAttention,
  newMetric,
} from "../lib/semantic";
import type { ChatSession, Issue, SemColumn, SemMetric, SemModel, VersionDetail } from "../lib/types";

type Tab = "columns" | "metrics" | "entity" | "diff" | "json";

const ROLE_OPTS = ["dimension", "measure", "time_dimension", "identifier", "ignore"].map((v) => ({
  value: v,
  label: v.replace("_", " "),
}));
const TYPE_OPTS = ["string", "integer", "float", "boolean", "date", "datetime"].map((v) => ({
  value: v,
  label: v,
}));
const AGG_OPTS = [
  { value: "", label: "—" },
  ...["sum", "avg", "min", "max", "count", "count_distinct"].map((v) => ({ value: v, label: v })),
];

const listToText = (v: string[] | null) => (v ?? []).join(", ");
const textToList = (v: string) =>
  v
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

function ConfidenceBadge({ c, rationale }: { c: string | null; rationale: string | null }) {
  if (!c) return null;
  return (
    <span title={rationale ?? undefined}>
      <Badge tone={c === "high" ? "green" : c === "medium" ? "amber" : "red"}>{c}</Badge>
    </span>
  );
}

function Changed({ on }: { on: boolean }) {
  return on ? (
    <span
      className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-amber-500 align-middle"
      title="Overridden (differs from the AI draft)"
    />
  ) : null;
}

function IssueList({ issues }: { issues: Issue[] }) {
  if (!issues.length) return null;
  return (
    <ul className="mt-1 space-y-0.5 text-xs">
      {issues.map((i, k) => (
        <li
          key={k}
          className={
            i.severity === "error" ? "text-rose-600 dark:text-rose-400" : "text-amber-700 dark:text-amber-400"
          }
        >
          {i.severity === "error" ? "✕" : "!"} {i.message}
        </li>
      ))}
    </ul>
  );
}

interface ItemActions {
  editable: boolean;
  onAccept: () => void;
  onRegenerate: () => void;
}

function ColumnRow({
  col,
  base,
  issues,
  onChange,
  actions,
}: {
  col: SemColumn;
  base?: SemColumn;
  issues: Issue[];
  onChange: (c: SemColumn) => void;
  actions: ItemActions;
}) {
  const [open, setOpen] = useState(false);
  const changed = new Set(changedFields(col, base, COLUMN_FIELDS));
  const set = (patch: Partial<SemColumn>) => onChange({ ...col, ...patch });
  const dis = !actions.editable;
  const regenerated = Object.values(col.provenance ?? {}).includes("ai_regenerated");
  return (
    <Fragment>
      <tr
        className={cx(
          "align-top",
          col.role === "ignore" && "opacity-60",
          issues.some((i) => i.severity === "error") && "bg-rose-50/60 dark:bg-rose-950/30",
        )}
      >
        <td className="px-2 py-2">
          <button className="text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
            <span className="mr-1 text-slate-400">{open ? "▾" : "▸"}</span>
            <span className="font-mono text-xs">{col.name}</span>
          </button>
          <div className="mt-1 flex flex-wrap gap-1">
            <ConfidenceBadge c={col.confidence} rationale={col.rationale} />
            {col.review_status === "accepted" ? <Badge tone="green">accepted</Badge> : <Badge>pending</Badge>}
            {changed.size > 0 && <Badge tone="amber">overridden</Badge>}
            {regenerated && <Badge tone="blue">regenerated</Badge>}
          </div>
          <IssueList issues={issues} />
        </td>
        <td className="px-2 py-2">
          <Input
            value={col.display_name}
            disabled={dis}
            onChange={(e) => set({ display_name: e.target.value })}
            className="py-1"
            aria-label={`${col.name} display name`}
          />
          <Changed on={changed.has("display_name")} />
        </td>
        <td className="px-2 py-2">
          <Select
            value={col.role}
            disabled={dis}
            onChange={(e) => set({ role: e.target.value as SemColumn["role"] })}
            options={ROLE_OPTS}
            className="py-1"
            aria-label={`${col.name} role`}
          />
          <Changed on={changed.has("role")} />
        </td>
        <td className="px-2 py-2">
          <Select
            value={col.data_type}
            disabled={dis}
            onChange={(e) => set({ data_type: e.target.value as SemColumn["data_type"] })}
            options={TYPE_OPTS}
            className="py-1"
            aria-label={`${col.name} type`}
          />
          <Changed on={changed.has("data_type")} />
        </td>
        <td className="px-2 py-2">
          <Select
            value={col.default_aggregation ?? ""}
            disabled={dis || col.role !== "measure"}
            onChange={(e) =>
              set({ default_aggregation: (e.target.value || null) as SemColumn["default_aggregation"] })
            }
            options={AGG_OPTS}
            className="py-1"
            aria-label={`${col.name} aggregation`}
          />
          <Changed on={changed.has("default_aggregation")} />
        </td>
        <td className="px-2 py-2 text-center">
          <input
            type="checkbox"
            checked={col.pii}
            disabled={dis}
            onChange={(e) => set({ pii: e.target.checked })}
            aria-label={`${col.name} is PII`}
            className="mt-2 h-4 w-4 rounded border-slate-300 text-rose-600"
          />
        </td>
        <td className="whitespace-nowrap px-2 py-2">
          {actions.editable && (
            <div className="flex gap-1">
              <Button
                size="sm"
                variant={col.review_status === "accepted" ? "ghost" : "secondary"}
                onClick={actions.onAccept}
                title="Accept"
              >
                ✅
              </Button>
              <Button size="sm" variant="ghost" onClick={actions.onRegenerate} title="Regenerate this column">
                🔄
              </Button>
            </div>
          )}
        </td>
      </tr>
      {open && (
        <tr className="bg-slate-50/70 dark:bg-slate-900/50">
          <td colSpan={7} className="px-3 pb-3 pt-1">
            <div className="grid gap-3 md:grid-cols-2">
              <Field label="Description">
                <Textarea
                  rows={2}
                  value={col.description}
                  disabled={dis}
                  onChange={(e) => set({ description: e.target.value })}
                />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Unit">
                  <Input
                    value={col.unit ?? ""}
                    disabled={dis}
                    onChange={(e) => set({ unit: e.target.value || null })}
                  />
                </Field>
                <Field label="Format">
                  <Input
                    value={col.format ?? ""}
                    disabled={dis}
                    onChange={(e) => set({ format: e.target.value || null })}
                  />
                </Field>
              </div>
              <Field label="Synonyms (comma-separated)">
                <Input
                  value={listToText(col.synonyms)}
                  disabled={dis}
                  onChange={(e) => set({ synonyms: textToList(e.target.value) })}
                />
              </Field>
              <Field label="Allowed values (comma-separated, optional)">
                <Input
                  value={listToText(col.allowed_values)}
                  disabled={dis}
                  onChange={(e) =>
                    set({ allowed_values: e.target.value.trim() ? textToList(e.target.value) : null })
                  }
                />
              </Field>
            </div>
            {col.rationale && <p className="mt-2 text-xs text-slate-500">AI rationale: {col.rationale}</p>}
            {changed.size > 0 && base && (
              <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                Overridden:{" "}
                {[...changed].map((f) => `${f} (was ${JSON.stringify((base as any)[f])})`).join("; ")}
              </p>
            )}
          </td>
        </tr>
      )}
    </Fragment>
  );
}

function MetricCard({
  metric,
  base,
  issues,
  versionId,
  model,
  onChange,
  onDelete,
  actions,
}: {
  metric: SemMetric;
  base?: SemMetric;
  issues: Issue[];
  versionId: string;
  model: SemModel;
  onChange: (m: SemMetric) => void;
  onDelete: () => void;
  actions: ItemActions;
}) {
  const [test, setTest] = useState<{ ok: boolean; value?: any; error?: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const set = (p: Partial<SemMetric>) => onChange({ ...metric, ...p });
  const changed = new Set(changedFields(metric, base, METRIC_FIELDS));
  const dis = !actions.editable;

  // P4-04: validate live (debounced) against the first 10k rows
  useEffect(() => {
    const t = setTimeout(async () => {
      setTesting(true);
      try {
        setTest(
          await api.post(`/api/semantic/${versionId}/test-metric`, {
            expression: metric.expression,
            filters: metric.filters,
            model,
          }),
        );
      } catch (e) {
        setTest({ ok: false, error: errorMessage(e) });
      } finally {
        setTesting(false);
      }
    }, 600);
    return () => clearTimeout(t);
    // model is only needed for column metadata; re-test on expression/filter edits
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [metric.expression, JSON.stringify(metric.filters), versionId]);

  return (
    <Card
      className={cx("p-4", issues.some((i) => i.severity === "error") && "ring-rose-300 dark:ring-rose-800")}
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-semibold">{metric.name}</span>
          <ConfidenceBadge c={metric.confidence} rationale={metric.rationale} />
          {metric.review_status === "accepted" ? (
            <Badge tone="green">accepted</Badge>
          ) : (
            <Badge>pending</Badge>
          )}
          {!base ? (
            <Badge tone="blue">added by you</Badge>
          ) : (
            changed.size > 0 && <Badge tone="amber">overridden</Badge>
          )}
        </div>
        {actions.editable && (
          <div className="flex gap-1">
            <Button
              size="sm"
              variant={metric.review_status === "accepted" ? "ghost" : "secondary"}
              onClick={actions.onAccept}
            >
              ✅ Accept
            </Button>
            {base && (
              <Button size="sm" variant="ghost" onClick={actions.onRegenerate}>
                🔄 Regenerate
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={onDelete}>
              Delete
            </Button>
          </div>
        )}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Name (snake_case)">
          <Input
            value={metric.name}
            disabled={dis || !!base}
            onChange={(e) => set({ name: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })}
          />
        </Field>
        <Field label="Display name">
          <Input
            value={metric.display_name}
            disabled={dis}
            onChange={(e) => set({ display_name: e.target.value })}
          />
        </Field>
        <div className="md:col-span-2">
          <Field label="Expression (DuckDB aggregate SQL)">
            <Input
              className="font-mono"
              value={metric.expression}
              disabled={dis}
              onChange={(e) => set({ expression: e.target.value })}
            />
          </Field>
        </div>
        <Field label="Filters (one SQL condition per line)">
          <Textarea
            rows={2}
            className="font-mono text-xs"
            value={metric.filters.join("\n")}
            disabled={dis}
            onChange={(e) => set({ filters: e.target.value.split("\n").filter((l) => l.trim()) })}
          />
        </Field>
        <Field label="Description">
          <Textarea
            rows={2}
            value={metric.description}
            disabled={dis}
            onChange={(e) => set({ description: e.target.value })}
          />
        </Field>
        <Field label="Unit">
          <Input
            value={metric.unit ?? ""}
            disabled={dis}
            onChange={(e) => set({ unit: e.target.value || null })}
          />
        </Field>
        <Field label="Synonyms (comma-separated)">
          <Input
            value={listToText(metric.synonyms)}
            disabled={dis}
            onChange={(e) => set({ synonyms: textToList(e.target.value) })}
          />
        </Field>
      </div>
      <div className="mt-3 text-xs">
        {testing ? (
          <span className="text-slate-500">Testing…</span>
        ) : test?.ok ? (
          <span className="text-emerald-700 dark:text-emerald-400">
            ✓ Valid · on the first 10,000 rows = {formatNumber(test.value)}
          </span>
        ) : test ? (
          <span className="text-rose-600">✕ {test.error}</span>
        ) : null}
      </div>
      <IssueList issues={issues.filter((i) => !test || i.message.indexOf("Expression failed") < 0)} />
    </Card>
  );
}

export default function ReviewPage() {
  const { versionId = "" } = useParams();
  const nav = useNavigate();
  const [v, setV] = useState<VersionDetail | null>(null);
  const [model, setModel] = useState<SemModel | null>(null);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [dirty, setDirty] = useState(false);
  const [tab, setTab] = useState<Tab>("columns");
  const [attentionOnly, setAttentionOnly] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [jsonText, setJsonText] = useState("");
  const [regen, setRegen] = useState<{ type: "column" | "metric"; name: string } | null>(null);
  const [regenText, setRegenText] = useState("");
  const [testSession, setTestSession] = useState<ChatSession | null>(null);
  const [sourceName, setSourceName] = useState("");

  const apply = useCallback((d: VersionDetail) => {
    setV(d);
    setModel(d.model);
    setIssues(d.issues);
    setDirty(false);
  }, []);

  useEffect(() => {
    api
      .get<VersionDetail>(`/api/semantic/${versionId}`)
      .then((d) => {
        apply(d);
        api.get<any>(`/api/sources/${d.source_id}`).then((s) => setSourceName(s.source.name));
      })
      .catch((e) => setError(errorMessage(e)));
  }, [versionId, apply]);

  useEffect(() => {
    const h = (e: BeforeUnloadEvent) => {
      if (dirty) e.preventDefault();
    };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  const editable = v?.status === "draft";
  const update = (m: SemModel) => {
    setModel(m);
    setDirty(true);
  };

  const save = async (m: SemModel | null = model): Promise<VersionDetail | null> => {
    if (!m) return null;
    setBusy("save");
    setError(null);
    try {
      const d = await api.put<VersionDetail>(`/api/semantic/${versionId}`, { model: m });
      apply(d);
      return d;
    } catch (e: any) {
      setError(errorMessage(e));
      if (e.detail?.issues) setIssues(e.detail.issues);
      return null;
    } finally {
      setBusy(null);
    }
  };

  const approve = async () => {
    if (dirty && !(await save())) return;
    setBusy("approve");
    setError(null);
    try {
      const r = await api.post<{ source: { id: string } }>(`/api/semantic/${versionId}/approve`);
      nav(`/sources/${r.source.id}?approved=1`);
    } catch (e: any) {
      if (e.detail?.issues) setIssues(e.detail.issues);
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const acceptAll = async () => {
    if (dirty && !(await save())) return;
    apply(await api.post<VersionDetail>(`/api/semantic/${versionId}/accept-all`));
  };

  const runRegenerate = async () => {
    if (!regen) return;
    if (dirty && !(await save())) return;
    setBusy("regen");
    try {
      apply(
        await api.post<VersionDetail>(`/api/semantic/${versionId}/regenerate-item`, {
          item_type: regen.type,
          name: regen.name,
          instructions: regenText,
        }),
      );
      setNotice(`Regenerated ${regen.type} ${regen.name}.`);
      setRegen(null);
      setRegenText("");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const startTestDrive = async () => {
    if (!v) return;
    if (dirty && !(await save())) return;
    try {
      setTestSession(
        await api.post<ChatSession>("/api/chat/sessions", {
          source_id: v.source_id,
          semantic_version_id: versionId,
        }),
      );
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  const baseCols = useMemo(() => new Map((v?.base.columns ?? []).map((c) => [c.name, c])), [v]);
  const baseMetrics = useMemo(() => new Map((v?.base.metrics ?? []).map((m) => [m.name, m])), [v]);
  const diff = useMemo(() => (v && model ? diffModels(v.base, model) : []), [v, model]);
  const errors = issues.filter((i) => i.severity === "error");

  const columns = useMemo(() => {
    if (!model) return [];
    const withIssues = model.columns.map((c, idx) => ({ c, idx, iss: issuesFor(issues, "columns", c.name) }));
    // P4-02: low-confidence and problematic items first
    return withIssues
      .filter(({ c, iss }) => !attentionOnly || needsAttention(c, iss))
      .sort((a, b) => attentionRank(a.c, a.iss) - attentionRank(b.c, b.iss) || a.idx - b.idx);
  }, [model, issues, attentionOnly]);

  if (error && !v) return <Alert>{error}</Alert>;
  if (!v || !model) return <Spinner />;

  const setCol = (idx: number, c: SemColumn) =>
    update({ ...model, columns: model.columns.map((x, i) => (i === idx ? c : x)) });
  const setMetric = (idx: number, m: SemMetric) =>
    update({ ...model, metrics: model.metrics.map((x, i) => (i === idx ? m : x)) });
  const pending =
    model.columns.filter((c) => c.review_status !== "accepted").length +
    model.metrics.filter((m) => m.review_status !== "accepted").length;

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title={`Review semantic model · ${sourceName || model.entity.display_name}`}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            v{v.version} <StatusBadge status={v.status} /> origin {v.origin}
            {v.generated_by_model && ` · generated by ${v.generated_by_provider} / ${v.generated_by_model}`}
            {v.prompt_version && ` · ${v.prompt_version}`}
          </span>
        }
        actions={
          <>
            <Link to={`/sources/${v.source_id}`}>
              <Button variant="ghost">Source</Button>
            </Link>
            <a href={`/api/semantic/${versionId}/export`}>
              <Button variant="ghost">Export YAML</Button>
            </a>
            {editable && (
              <>
                <Button onClick={startTestDrive}>Test drive</Button>
                <Button onClick={acceptAll} disabled={!pending}>
                  Accept all remaining ({pending})
                </Button>
                <Button onClick={() => save()} loading={busy === "save"} disabled={!dirty}>
                  Save draft
                </Button>
                <Button
                  variant="primary"
                  onClick={approve}
                  loading={busy === "approve"}
                  disabled={errors.length > 0 && !dirty}
                >
                  Approve
                </Button>
              </>
            )}
          </>
        }
      />
      {!editable && (
        <div className="mb-4">
          <Alert tone="blue">
            This version is {v.status} and read-only. Create a new draft from the source page to edit.
          </Alert>
        </div>
      )}
      {error && (
        <div className="mb-4">
          <Alert>{error}</Alert>
        </div>
      )}
      {notice && (
        <div className="mb-4" onClick={() => setNotice(null)}>
          <Alert tone="green">{notice}</Alert>
        </div>
      )}
      {editable && (
        <div className="mb-4">
          {errors.length > 0 ? (
            <Alert>
              <strong>{errors.length} error(s) block approval.</strong>{" "}
              {errors
                .slice(0, 4)
                .map((e) => `${e.path}: ${e.message}`)
                .join(" · ")}
              {errors.length > 4 && " …"}
            </Alert>
          ) : (
            <Alert tone="green">
              No blocking errors{issues.length ? ` · ${issues.length} warning(s)` : ""}.{" "}
              {dirty && "Unsaved changes are validated when you save."}
            </Alert>
          )}
        </div>
      )}

      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "columns", label: `Columns (${model.columns.length})` },
          { id: "metrics", label: `Metrics (${model.metrics.length})` },
          { id: "entity", label: "Entity & questions" },
          { id: "diff", label: `Diff vs AI draft (${diff.length})` },
          { id: "json", label: "Raw JSON" },
        ]}
      />

      {tab === "columns" && (
        <Card>
          <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2 dark:border-slate-700">
            <Toggle checked={attentionOnly} onChange={setAttentionOnly} label="Needs attention only" />
            <span className="text-xs text-slate-500">
              Sorted: errors, low confidence, warnings, then pending
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="text-left text-xs text-slate-500">
                <tr>
                  <th className="px-2 py-2">Column</th>
                  <th className="px-2 py-2">Display name</th>
                  <th className="px-2 py-2">Role</th>
                  <th className="px-2 py-2">Type</th>
                  <th className="px-2 py-2">Aggregation</th>
                  <th className="px-2 py-2">PII</th>
                  <th className="px-2 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {columns.map(({ c, idx, iss }) => (
                  <ColumnRow
                    key={c.name}
                    col={c}
                    base={baseCols.get(c.name)}
                    issues={iss}
                    onChange={(nc) => setCol(idx, nc)}
                    actions={{
                      editable,
                      onAccept: () => setCol(idx, { ...c, review_status: "accepted" }),
                      onRegenerate: () => setRegen({ type: "column", name: c.name }),
                    }}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {tab === "metrics" && (
        <div className="space-y-3">
          {model.metrics.map((m, idx) => (
            <MetricCard
              key={idx}
              metric={m}
              base={baseMetrics.get(m.name)}
              issues={issuesFor(issues, "metrics", m.name)}
              versionId={versionId}
              model={model}
              onChange={(nm) => setMetric(idx, nm)}
              onDelete={() => update({ ...model, metrics: model.metrics.filter((_, i) => i !== idx) })}
              actions={{
                editable,
                onAccept: () => setMetric(idx, { ...m, review_status: "accepted" }),
                onRegenerate: () => setRegen({ type: "metric", name: m.name }),
              }}
            />
          ))}
          {editable && (
            <Button onClick={() => update({ ...model, metrics: [...model.metrics, newMetric()] })}>
              + Add metric
            </Button>
          )}
        </div>
      )}

      {tab === "entity" && (
        <Card className="space-y-3 p-4">
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Entity name">
              <Input
                value={model.entity.name}
                disabled={!editable}
                onChange={(e) => update({ ...model, entity: { ...model.entity, name: e.target.value } })}
              />
            </Field>
            <Field label="Display name">
              <Input
                value={model.entity.display_name}
                disabled={!editable}
                onChange={(e) =>
                  update({ ...model, entity: { ...model.entity, display_name: e.target.value } })
                }
              />
            </Field>
            <Field label="Description">
              <Textarea
                rows={2}
                value={model.entity.description}
                disabled={!editable}
                onChange={(e) =>
                  update({ ...model, entity: { ...model.entity, description: e.target.value } })
                }
              />
            </Field>
            <Field label="Grain (what one row is)">
              <Textarea
                rows={2}
                value={model.entity.grain}
                disabled={!editable}
                onChange={(e) => update({ ...model, entity: { ...model.entity, grain: e.target.value } })}
              />
            </Field>
          </div>
          <Field label="Sample questions (one per line)" hint="Shown as conversation starters in chat.">
            <Textarea
              rows={7}
              value={model.sample_questions.join("\n")}
              disabled={!editable}
              onChange={(e) =>
                update({ ...model, sample_questions: e.target.value.split("\n").filter((l) => l.trim()) })
              }
            />
          </Field>
        </Card>
      )}

      {tab === "diff" && (
        <Card className="p-4">
          {diff.length === 0 ? (
            <p className="text-sm text-slate-500">
              No changes from the baseline{v.prompt_version ? " AI draft" : ""}.
            </p>
          ) : (
            <table className="min-w-full text-sm">
              <thead className="text-left text-xs text-slate-500">
                <tr>
                  <th className="py-1 pr-3">Item</th>
                  <th className="py-1 pr-3">Field</th>
                  <th className="py-1 pr-3">AI draft</th>
                  <th className="py-1">Now</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {diff.map((d, i) => (
                  <tr key={i} className="align-top">
                    <td className="py-1.5 pr-3 font-mono text-xs">
                      {d.kind}:{d.name}
                    </td>
                    <td className="py-1.5 pr-3 text-xs">{d.field}</td>
                    <td className="py-1.5 pr-3 text-xs text-rose-700 line-through decoration-rose-300 dark:text-rose-400">
                      {JSON.stringify(d.before)}
                    </td>
                    <td className="py-1.5 text-xs text-emerald-700 dark:text-emerald-400">
                      {JSON.stringify(d.after)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      {tab === "json" && (
        <Card className="space-y-3 p-4">
          <p className="text-sm text-slate-500">
            Advanced: edit the whole model. It is validated against the schema when you apply it.
          </p>
          <Textarea
            rows={24}
            className="font-mono text-xs"
            value={jsonText || JSON.stringify(model, null, 2)}
            disabled={!editable}
            onChange={(e) => setJsonText(e.target.value)}
          />
          {editable && (
            <div className="flex gap-2">
              <Button
                variant="primary"
                onClick={async () => {
                  try {
                    const parsed = JSON.parse(jsonText || JSON.stringify(model));
                    const d = await save(parsed);
                    if (d) {
                      setJsonText("");
                      setNotice("JSON applied and saved.");
                    }
                  } catch (e) {
                    setError(`Invalid JSON: ${errorMessage(e)}`);
                  }
                }}
              >
                Apply & save
              </Button>
              <Button onClick={() => setJsonText("")}>Reset</Button>
            </div>
          )}
        </Card>
      )}

      <Modal
        open={!!regen}
        onClose={() => setRegen(null)}
        title={`Regenerate ${regen?.type} “${regen?.name}”`}
      >
        <div className="space-y-3">
          <p className="text-sm text-slate-500">
            The LLM proposes a new version of this item from its profile. Unsaved edits are saved first.
          </p>
          <Field label="Instructions (optional)">
            <Textarea
              rows={3}
              value={regenText}
              onChange={(e) => setRegenText(e.target.value)}
              placeholder="e.g. This is the net amount in GBP, excluding VAT."
            />
          </Field>
          <div className="flex justify-end gap-2">
            <Button onClick={() => setRegen(null)}>Cancel</Button>
            <Button variant="primary" onClick={runRegenerate} loading={busy === "regen"}>
              Regenerate
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={!!testSession}
        onClose={() => setTestSession(null)}
        title="Test drive (against this draft)"
        wide
      >
        {testSession && (
          <div className="h-[65vh]">
            <ChatPanel sessionId={testSession.id} starters={model.sample_questions.slice(0, 3)} compact />
          </div>
        )}
      </Modal>
    </div>
  );
}
