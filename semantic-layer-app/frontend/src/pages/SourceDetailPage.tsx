import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import GeneratePanel from "../components/GeneratePanel";
import ProfileTable from "../components/ProfileTable";
import { Alert, Badge, Button, Card, Modal, PageHeader, Spinner, StatusBadge, Tabs } from "../components/ui";
import { api, errorMessage, formatBytes, timeAgo, uploadFile } from "../lib/api";
import type { SourceDetail, VersionDetail } from "../lib/types";

type Tab = "model" | "profile" | "versions" | "data";

function ActiveModel({ versionId }: { versionId: string }) {
  const [v, setV] = useState<VersionDetail | null>(null);
  useEffect(() => {
    api.get<VersionDetail>(`/api/semantic/${versionId}`).then(setV);
  }, [versionId]);
  if (!v) return <Spinner />;
  const m = v.model;
  return (
    <div className="space-y-4">
      <Card className="p-4">
        <h3 className="font-semibold">{m.entity.display_name}</h3>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{m.entity.description}</p>
        <p className="mt-1 text-xs text-slate-500">Grain: {m.entity.grain}</p>
      </Card>
      <Card className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs text-slate-500 dark:bg-slate-800">
            <tr>
              <th className="px-3 py-2">Column</th>
              <th className="px-3 py-2">Role</th>
              <th className="px-3 py-2">Type</th>
              <th className="px-3 py-2">Description</th>
              <th className="px-3 py-2">Synonyms</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {m.columns.map((c) => (
              <tr key={c.name} className={c.role === "ignore" ? "opacity-50" : ""}>
                <td className="px-3 py-2">
                  <div className="font-medium">{c.display_name}</div>
                  <div className="font-mono text-xs text-slate-400">{c.name}</div>
                </td>
                <td className="px-3 py-2">
                  <Badge
                    tone={c.role === "measure" ? "indigo" : c.role === "time_dimension" ? "blue" : "slate"}
                  >
                    {c.role.replace("_", " ")}
                  </Badge>
                  {c.pii && (
                    <span className="ml-1">
                      <Badge tone="red">PII</Badge>
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-xs">
                  {c.data_type}
                  {c.default_aggregation && ` · ${c.default_aggregation}`}
                  {c.unit && ` · ${c.unit}`}
                </td>
                <td className="max-w-md px-3 py-2 text-xs text-slate-600 dark:text-slate-400">
                  {c.description}
                </td>
                <td className="px-3 py-2 text-xs text-slate-500">{c.synonyms.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <div className="grid gap-4 md:grid-cols-2">
        <Card className="p-4">
          <h3 className="mb-2 font-semibold">Metrics</h3>
          <ul className="space-y-2 text-sm">
            {m.metrics.map((x) => (
              <li key={x.name}>
                <span className="font-medium">{x.display_name || x.name}</span>{" "}
                <code className="text-xs text-slate-500">{x.expression}</code>
                {x.filters.length > 0 && (
                  <span className="text-xs text-slate-500"> where {x.filters.join(" and ")}</span>
                )}
                <div className="text-xs text-slate-500">{x.description}</div>
              </li>
            ))}
          </ul>
        </Card>
        <Card className="p-4">
          <h3 className="mb-2 font-semibold">Sample questions</h3>
          <ul className="list-inside list-disc space-y-1 text-sm text-slate-700 dark:text-slate-300">
            {m.sample_questions.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}

export default function SourceDetailPage() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [d, setD] = useState<SourceDetail | null>(null);
  const [tab, setTab] = useState<Tab>("model");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(
    params.get("approved") ? "Approved. This source is onboarded and available in chat." : null,
  );
  const [regenOpen, setRegenOpen] = useState(false);
  const [refreshPct, setRefreshPct] = useState<number | null>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const refreshRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    api
      .get<SourceDetail>(`/api/sources/${id}`)
      .then((x) => {
        setD(x);
        if (!x.source.active_semantic_version_id) setTab((t) => (t === "model" ? "versions" : t));
      })
      .catch((e) => setError(errorMessage(e)));
  }, [id]);
  useEffect(load, [load]);

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setError(null);
    try {
      await fn();
      if (ok) setNotice(ok);
      load();
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  if (error && !d) return <Alert>{error}</Alert>;
  if (!d) return <Spinner />;
  const s = d.source;
  const draft = d.versions.find((v) => v.status === "draft");

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title={s.name}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge status={s.status} />
            {s.original_filename} · {s.row_count?.toLocaleString() ?? "—"} rows · {s.column_count ?? "—"}{" "}
            columns · {formatBytes(s.size_bytes)} · updated {timeAgo(s.updated_at)}
          </span>
        }
        actions={
          <>
            {s.status === "onboarded" && (
              <Link to="/chat">
                <Button variant="primary">Chat</Button>
              </Link>
            )}
            {draft ? (
              <Link to={`/review/${draft.id}`}>
                <Button>Continue review (v{draft.version})</Button>
              </Link>
            ) : s.active_semantic_version_id ? (
              <Button
                onClick={() =>
                  act(async () =>
                    nav(`/review/${(await api.post<VersionDetail>(`/api/sources/${id}/semantic/draft`)).id}`),
                  )
                }
              >
                Edit model (new draft)
              </Button>
            ) : (
              <Link to={`/sources/${id}/onboard`}>
                <Button>Continue onboarding</Button>
              </Link>
            )}
          </>
        }
      />
      {notice && (
        <div className="mb-4" onClick={() => setNotice(null)}>
          <Alert tone="green">{notice}</Alert>
        </div>
      )}
      {error && (
        <div className="mb-4">
          <Alert>{error}</Alert>
        </div>
      )}
      {s.description && <p className="mb-4 text-sm text-slate-600 dark:text-slate-400">{s.description}</p>}
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "model", label: s.active_version ? `Active model (v${s.active_version})` : "Active model" },
          { id: "profile", label: "Profile" },
          { id: "versions", label: `Versions (${d.versions.length})` },
          { id: "data", label: "Data & lifecycle" },
        ]}
      />
      {tab === "model" &&
        (s.active_semantic_version_id ? (
          <ActiveModel versionId={s.active_semantic_version_id} />
        ) : (
          <Alert tone="amber">No approved semantic model yet.</Alert>
        ))}
      {tab === "profile" && d.profile && (
        <Card className="p-4">
          <ProfileTable profile={d.profile} />
        </Card>
      )}
      {tab === "versions" && (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => setRegenOpen(true)}>Regenerate with AI</Button>
            <Button onClick={() => importRef.current?.click()}>Import YAML/JSON</Button>
            <input
              ref={importRef}
              type="file"
              accept=".yaml,.yml,.json"
              className="hidden"
              onChange={async (e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (f)
                  act(async () =>
                    nav(
                      `/review/${(await api.postText<VersionDetail>(`/api/sources/${id}/semantic/import`, await f.text())).id}`,
                    ),
                  );
              }}
            />
          </div>
          <Card className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs text-slate-500 dark:bg-slate-800">
                <tr>
                  <th className="px-3 py-2">Version</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Origin</th>
                  <th className="px-3 py-2">Generated by</th>
                  <th className="px-3 py-2">Notes</th>
                  <th className="px-3 py-2">Updated</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {d.versions.map((v) => (
                  <tr key={v.id}>
                    <td className="px-3 py-2 font-medium">
                      v{v.version}{" "}
                      {v.id === s.active_semantic_version_id && <Badge tone="green">active</Badge>}
                    </td>
                    <td className="px-3 py-2">
                      <StatusBadge status={v.status} />
                    </td>
                    <td className="px-3 py-2 text-xs">{v.origin}</td>
                    <td className="px-3 py-2 text-xs text-slate-500">
                      {v.generated_by_model ? `${v.generated_by_provider} / ${v.generated_by_model}` : "—"}
                    </td>
                    <td className="max-w-xs truncate px-3 py-2 text-xs text-slate-500">{v.change_notes}</td>
                    <td className="px-3 py-2 text-xs text-slate-500">{timeAgo(v.updated_at)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right">
                      <Link to={`/review/${v.id}`}>
                        <Button size="sm" variant="ghost">
                          {v.status === "draft" ? "Review" : "View"}
                        </Button>
                      </Link>
                      <a href={`/api/semantic/${v.id}/export`}>
                        <Button size="sm" variant="ghost">
                          YAML
                        </Button>
                      </a>
                      {v.status === "superseded" && v.approved_at && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() =>
                            act(
                              () => api.post(`/api/semantic/${v.id}/rollback`),
                              `Rolled back to v${v.version}.`,
                            )
                          }
                        >
                          Roll back to this
                        </Button>
                      )}
                      {v.status === "draft" && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() =>
                            confirm(`Discard draft v${v.version}?`) &&
                            act(() => api.del(`/api/semantic/${v.id}`), "Draft discarded.")
                          }
                        >
                          Discard
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      )}
      {tab === "data" && (
        <div className="grid gap-4 md:grid-cols-2">
          <Card className="space-y-3 p-4">
            <h3 className="font-semibold">Refresh data</h3>
            <p className="text-sm text-slate-500">
              Upload a new version of the file. If the columns and types match, the semantic model is kept.
              Otherwise a draft is created for review and chat pauses until it is approved.
            </p>
            <Button
              onClick={() => refreshRef.current?.click()}
              loading={refreshPct !== null}
              disabled={!s.row_count}
            >
              {refreshPct !== null ? `Uploading ${refreshPct}%` : "Upload new file"}
            </Button>
            <input
              ref={refreshRef}
              type="file"
              accept=".csv,.tsv,.txt"
              className="hidden"
              onChange={async (e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (!f) return;
                setRefreshPct(0);
                await act(async () => {
                  const r = await uploadFile<{
                    compatible: boolean;
                    schema_diff: Record<string, string[]>;
                    draft_version_id: string | null;
                  }>(`/api/sources/${id}/refresh`, f, setRefreshPct);
                  const diff = Object.entries(r.schema_diff)
                    .filter(([, v]) => v.length)
                    .map(([k, v]) => `${k}: ${v.join(", ")}`)
                    .join("; ");
                  setNotice(
                    r.compatible
                      ? "Data refreshed. Schema unchanged, so the semantic model was kept."
                      : `Schema changed (${diff}). Review the new draft.`,
                  );
                  if (r.draft_version_id) nav(`/review/${r.draft_version_id}`);
                });
                setRefreshPct(null);
              }}
            />
          </Card>
          <Card className="space-y-3 p-4">
            <h3 className="font-semibold">Lifecycle</h3>
            {s.status === "archived" ? (
              <Button onClick={() => act(() => api.post(`/api/sources/${id}/unarchive`), "Restored.")}>
                Unarchive
              </Button>
            ) : (
              <Button
                onClick={() =>
                  act(() => api.post(`/api/sources/${id}/archive`), "Archived. It no longer appears in chat.")
                }
              >
                Archive
              </Button>
            )}
            <div>
              <Button
                variant="danger"
                onClick={() =>
                  confirm(`Permanently delete “${s.name}”, its files, semantic versions and chats?`) &&
                  act(async () => {
                    await api.del(`/api/sources/${id}`);
                    nav("/sources");
                  })
                }
              >
                Delete source
              </Button>
              <p className="mt-1 text-xs text-slate-500">
                Removes the original CSV, the Parquet copy, all versions and chats.
              </p>
            </div>
          </Card>
        </div>
      )}
      <Modal open={regenOpen} onClose={() => setRegenOpen(false)} title="Regenerate the semantic model" wide>
        <GeneratePanel sourceId={id} onDone={(vid) => nav(`/review/${vid}`)} />
      </Modal>
    </div>
  );
}
