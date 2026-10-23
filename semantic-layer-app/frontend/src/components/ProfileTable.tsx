import { Fragment, useState } from "react";
import { formatNumber } from "../lib/api";
import type { Profile } from "../lib/types";
import { Badge, DataTable } from "./ui";

/** Per-column profile view (P2-14). */
export default function ProfileTable({ profile }: { profile: Profile }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div>
      <p className="mb-2 text-sm text-slate-500">
        {profile.row_count.toLocaleString()} rows · {profile.columns.length} columns · profiled in{" "}
        {profile.profile_ms} ms
        {profile.sampled && ` on a ${profile.sample_rows.toLocaleString()}-row sample (row count is exact)`}
        {profile.candidate_keys.length > 0 && ` · candidate key: ${profile.candidate_keys.join(", ")}`}
      </p>
      <div className="overflow-x-auto rounded-md ring-1 ring-slate-200 dark:ring-slate-700">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs text-slate-500 dark:bg-slate-800">
            <tr>
              <th className="px-3 py-2">Column</th>
              <th className="px-3 py-2">Type</th>
              <th className="px-3 py-2 text-right">Null %</th>
              <th className="px-3 py-2 text-right">Distinct</th>
              <th className="px-3 py-2">Range / mean</th>
              <th className="px-3 py-2">Hints</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {profile.columns.map((c) => (
              <Fragment key={c.name}>
                <tr
                  className="cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/50"
                  onClick={() => setOpen(open === c.name ? null : c.name)}
                  aria-expanded={open === c.name}
                >
                  <td className="px-3 py-2">
                    <div className="font-mono text-xs">{c.name}</div>
                    {c.original_name !== c.name && (
                      <div className="text-xs text-slate-400">{c.original_name}</div>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {c.inferred_type} <span className="text-slate-400">({c.storage_type})</span>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{c.null_pct}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {c.distinct_is_estimate ? "≈" : ""}
                    {c.distinct_count.toLocaleString()}
                  </td>
                  <td className="max-w-[16rem] truncate px-3 py-2 text-xs">
                    {c.pii ? (
                      <span className="text-slate-400">hidden (PII)</span>
                    ) : (
                      <>
                        {formatNumber(c.min)} → {formatNumber(c.max)}
                        {c.mean != null && (
                          <span className="text-slate-400"> · mean {formatNumber(c.mean)}</span>
                        )}
                      </>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {c.pii && <Badge tone="red">PII</Badge>}
                      {c.candidate_key && <Badge tone="indigo">key</Badge>}
                      {c.pattern_hints.map((h) => (
                        <Badge key={h}>{h}</Badge>
                      ))}
                    </div>
                  </td>
                </tr>
                {open === c.name && (
                  <tr>
                    <td colSpan={6} className="bg-slate-50 px-3 py-3 dark:bg-slate-900/60">
                      <div className="grid gap-4 md:grid-cols-2">
                        <div>
                          <p className="mb-1 text-xs font-semibold text-slate-500">Top values</p>
                          {c.top_values.length ? (
                            <DataTable
                              columns={["value", "count"]}
                              rows={c.top_values.map((t) => [c.pii ? "•••" : t.value, t.count])}
                              maxHeight="12rem"
                            />
                          ) : (
                            <p className="text-xs text-slate-400">High-cardinality column</p>
                          )}
                        </div>
                        <div className="space-y-1 text-xs text-slate-600 dark:text-slate-400">
                          <p className="font-semibold text-slate-500">Details</p>
                          {c.median != null && (
                            <p>
                              Median {formatNumber(c.median)} · std {formatNumber(c.std)}
                            </p>
                          )}
                          {c.length && (
                            <p>
                              Length {c.length.min}–{c.length.max} (avg {formatNumber(c.length.avg)})
                            </p>
                          )}
                          {!c.pii && (
                            <p className="break-words">
                              Samples:{" "}
                              {c.sample_values
                                .slice(0, 8)
                                .map((v) => String(v))
                                .join(" · ")}
                            </p>
                          )}
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
