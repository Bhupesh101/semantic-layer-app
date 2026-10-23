import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ToolResultUI } from "../lib/types";
import { Button, DataTable } from "./ui";

const COLORS = ["#4f46e5", "#0891b2", "#d97706", "#db2777", "#059669"];

/** A chart is offered when the result is one time/category dimension plus 1–5 numeric metrics (P7-05). */
function chartShape(r: ToolResultUI) {
  const meta = r.column_meta ?? [];
  const dims = meta.filter((m) => m.kind !== "metric");
  const metrics = meta.filter((m) => m.kind === "metric");
  if (!r.rows || r.rows.length < 2 || dims.length !== 1 || metrics.length < 1 || metrics.length > 5)
    return null;
  const cols = r.columns ?? [];
  const xIdx = cols.indexOf(dims[0].name);
  const yIdx = metrics.map((m) => cols.indexOf(m.name));
  if (xIdx < 0 || yIdx.some((i) => i < 0)) return null;
  if (!r.rows.every((row) => yIdx.every((i) => typeof row[i] === "number" || row[i] === null))) return null;
  return { kind: dims[0].kind === "time" ? "line" : "bar", x: dims[0], metrics, xIdx, yIdx };
}

export default function ResultView({ result }: { result: ToolResultUI }) {
  const shape = chartShape(result);
  const [view, setView] = useState<"table" | "chart">(shape && result.rows!.length > 3 ? "chart" : "table");
  if (!result.columns || !result.rows) return null;
  const data = shape
    ? result.rows.map((row) =>
        Object.fromEntries([
          ["x", row[shape.xIdx]],
          ...shape.metrics.map((m, k) => [m.name, row[shape.yIdx[k]]]),
        ]),
      )
    : [];
  const fmt = (v: any) =>
    typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : v;

  return (
    <div className="mt-2">
      <div className="mb-1 flex items-center justify-between text-xs text-slate-500">
        <span>
          {result.rows.length} of {result.total_rows?.toLocaleString()} row(s)
          {result.truncated && " · truncated"}
        </span>
        {shape && (
          <div className="flex gap-1">
            <Button
              size="sm"
              variant={view === "chart" ? "secondary" : "ghost"}
              onClick={() => setView("chart")}
            >
              Chart
            </Button>
            <Button
              size="sm"
              variant={view === "table" ? "secondary" : "ghost"}
              onClick={() => setView("table")}
            >
              Table
            </Button>
          </div>
        )}
      </div>
      {view === "chart" && shape ? (
        <div className="h-64 w-full rounded-md bg-white p-2 ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700">
          <ResponsiveContainer>
            {shape.kind === "line" ? (
              <LineChart data={data} margin={{ left: 8, right: 8, top: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="x" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt} width={70} />
                <Tooltip formatter={fmt} />
                {shape.metrics.map((m, i) => (
                  <Line
                    key={m.name}
                    type="monotone"
                    dataKey={m.name}
                    name={m.display_name}
                    stroke={COLORS[i]}
                    dot={false}
                    strokeWidth={2}
                  />
                ))}
              </LineChart>
            ) : (
              <BarChart data={data} margin={{ left: 8, right: 8, top: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis
                  dataKey="x"
                  tick={{ fontSize: 11 }}
                  interval={0}
                  angle={data.length > 8 ? -30 : 0}
                  textAnchor={data.length > 8 ? "end" : "middle"}
                  height={data.length > 8 ? 60 : 30}
                />
                <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt} width={70} />
                <Tooltip formatter={fmt} />
                {shape.metrics.map((m, i) => (
                  <Bar key={m.name} dataKey={m.name} name={m.display_name} fill={COLORS[i]} />
                ))}
              </BarChart>
            )}
          </ResponsiveContainer>
        </div>
      ) : (
        <DataTable columns={result.columns} rows={result.rows} maxHeight="18rem" />
      )}
    </div>
  );
}
