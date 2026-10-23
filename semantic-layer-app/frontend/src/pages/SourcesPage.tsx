import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Alert, Button, EmptyState, Input, PageHeader, Select, StatusBadge } from "../components/ui";
import { api, errorMessage, formatBytes, timeAgo } from "../lib/api";
import type { Source } from "../lib/types";

export default function SourcesPage() {
  const [sources, setSources] = useState<Source[] | null>(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("active");
  const [error, setError] = useState<string | null>(null);
  const nav = useNavigate();

  useEffect(() => {
    api
      .get<Source[]>("/api/sources")
      .then(setSources)
      .catch((e) => setError(errorMessage(e)));
  }, []);

  const shown = useMemo(
    () =>
      (sources ?? [])
        .filter((s) =>
          status === "active" ? s.status !== "archived" : status === "all" || s.status === status,
        )
        .filter(
          (s) =>
            !q || `${s.name} ${s.original_filename} ${s.description}`.toLowerCase().includes(q.toLowerCase()),
        ),
    [sources, q, status],
  );

  const open = (s: Source) =>
    nav(
      s.status === "onboarded" || s.status === "archived" ? `/sources/${s.id}` : `/sources/${s.id}/onboard`,
    );

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Data sources"
        subtitle="Upload a CSV, let the LLM propose a semantic layer from its profile, review it, then chat."
        actions={
          <Link to="/sources/new">
            <Button variant="primary">New data source</Button>
          </Link>
        }
      />
      {error && <Alert>{error}</Alert>}
      <div className="mb-4 flex flex-wrap gap-2">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search"
          className="max-w-xs"
          aria-label="Search sources"
        />
        <Select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="max-w-[12rem]"
          aria-label="Filter by status"
          options={[
            { value: "active", label: "All but archived" },
            { value: "onboarded", label: "Onboarded" },
            { value: "in_review", label: "In review" },
            { value: "profiled", label: "Profiled" },
            { value: "uploaded", label: "Uploaded" },
            { value: "failed", label: "Failed" },
            { value: "archived", label: "Archived" },
            { value: "all", label: "All" },
          ]}
        />
      </div>
      {sources && sources.length === 0 ? (
        <EmptyState title="No data sources yet">
          <Link to="/sources/new" className="text-indigo-600 hover:underline">
            Upload a CSV
          </Link>{" "}
          to get started.
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg bg-white ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs text-slate-500 dark:bg-slate-800">
              <tr>
                <th className="px-4 py-2">Name</th>
                <th className="px-4 py-2">Type</th>
                <th className="px-4 py-2 text-right">Rows</th>
                <th className="px-4 py-2 text-right">Columns</th>
                <th className="px-4 py-2 text-right">Size</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Active model</th>
                <th className="px-4 py-2">Updated</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {shown.map((s) => (
                <tr
                  key={s.id}
                  className="cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/50"
                  onClick={() => open(s)}
                >
                  <td className="px-4 py-2.5">
                    <button
                      className="text-left font-medium text-indigo-700 hover:underline dark:text-indigo-300"
                      onClick={() => open(s)}
                    >
                      {s.name}
                    </button>
                    <div className="text-xs text-slate-400">{s.original_filename}</div>
                  </td>
                  <td className="px-4 py-2.5 uppercase text-slate-500">{s.type}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">
                    {s.row_count?.toLocaleString() ?? "—"}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{s.column_count ?? "—"}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{formatBytes(s.size_bytes)}</td>
                  <td className="px-4 py-2.5">
                    <StatusBadge status={s.status} />
                  </td>
                  <td className="px-4 py-2.5">{s.active_version ? `v${s.active_version}` : "—"}</td>
                  <td className="px-4 py-2.5 text-slate-500">{timeAgo(s.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
