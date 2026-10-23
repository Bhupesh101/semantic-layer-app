import type { Issue, SemColumn, SemMetric, SemModel } from "./types";

export const COLUMN_FIELDS = [
  "display_name",
  "description",
  "data_type",
  "role",
  "default_aggregation",
  "unit",
  "format",
  "synonyms",
  "allowed_values",
  "pii",
] as const;
export const METRIC_FIELDS = [
  "display_name",
  "description",
  "expression",
  "filters",
  "unit",
  "synonyms",
] as const;
export const ENTITY_FIELDS = ["display_name", "description", "grain"] as const;

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/** Live provenance: a field differs from the baseline -> overridden. */
export function changedFields<T extends object>(
  item: T,
  base: T | undefined,
  fields: readonly string[],
): string[] {
  if (!base) return [...fields];
  return fields.filter((f) => !same((item as any)[f], (base as any)[f]));
}

export interface DiffEntry {
  kind: "entity" | "column" | "metric" | "questions";
  name: string;
  field: string;
  before: unknown;
  after: unknown;
}

/** P4-09: AI draft (baseline) vs current edits. */
export function diffModels(base: SemModel, cur: SemModel): DiffEntry[] {
  const out: DiffEntry[] = [];
  for (const f of ENTITY_FIELDS) {
    if (!same(base.entity[f], cur.entity[f]))
      out.push({
        kind: "entity",
        name: cur.entity.name,
        field: f,
        before: base.entity[f],
        after: cur.entity[f],
      });
  }
  const bcols = new Map(base.columns.map((c) => [c.name, c]));
  for (const c of cur.columns) {
    for (const f of changedFields(c, bcols.get(c.name), COLUMN_FIELDS)) {
      out.push({
        kind: "column",
        name: c.name,
        field: f,
        before: (bcols.get(c.name) as any)?.[f],
        after: (c as any)[f],
      });
    }
  }
  const bm = new Map(base.metrics.map((m) => [m.name, m]));
  const cm = new Map(cur.metrics.map((m) => [m.name, m]));
  for (const m of cur.metrics) {
    if (!bm.has(m.name))
      out.push({ kind: "metric", name: m.name, field: "(added)", before: null, after: m.expression });
    else
      for (const f of changedFields(m, bm.get(m.name), METRIC_FIELDS))
        out.push({
          kind: "metric",
          name: m.name,
          field: f,
          before: (bm.get(m.name) as any)[f],
          after: (m as any)[f],
        });
  }
  for (const m of base.metrics)
    if (!cm.has(m.name))
      out.push({ kind: "metric", name: m.name, field: "(removed)", before: m.expression, after: null });
  if (!same(base.sample_questions, cur.sample_questions))
    out.push({
      kind: "questions",
      name: "sample_questions",
      field: "",
      before: base.sample_questions,
      after: cur.sample_questions,
    });
  return out;
}

export function issuesFor(issues: Issue[], kind: "columns" | "metrics", name: string): Issue[] {
  return issues.filter((i) => i.path === `${kind}.${name}` || i.path.startsWith(`${kind}.${name}.`));
}

export function needsAttention(item: SemColumn | SemMetric, issues: Issue[]): boolean {
  return item.confidence === "low" || item.review_status !== "accepted" || issues.length > 0;
}

export function attentionRank(item: SemColumn | SemMetric, issues: Issue[]): number {
  if (issues.some((i) => i.severity === "error")) return 0;
  if (item.confidence === "low") return 1;
  if (issues.length) return 2;
  if (item.review_status !== "accepted") return item.confidence === "medium" ? 3 : 4;
  return 5;
}

export const newMetric = (): SemMetric => ({
  name: "new_metric",
  display_name: "New metric",
  description: "",
  expression: "COUNT(*)",
  filters: [],
  unit: null,
  synonyms: [],
  confidence: null,
  rationale: null,
  provenance: {},
  review_status: "accepted",
});
