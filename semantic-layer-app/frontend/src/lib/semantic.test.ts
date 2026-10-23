import { describe, expect, it } from "vitest";
import { attentionRank, changedFields, diffModels, needsAttention } from "./semantic";
import type { SemColumn, SemModel } from "./types";

const col = (over: Partial<SemColumn> = {}): SemColumn => ({
  name: "city",
  display_name: "City",
  description: "Delivery city",
  data_type: "string",
  role: "dimension",
  default_aggregation: null,
  unit: null,
  format: null,
  synonyms: [],
  allowed_values: null,
  pii: false,
  confidence: "high",
  rationale: null,
  provenance: {},
  review_status: "pending",
  ...over,
});

const model = (cols: SemColumn[], metrics: SemModel["metrics"] = []): SemModel => ({
  entity: { name: "orders", display_name: "Orders", description: "", grain: "" },
  columns: cols,
  metrics,
  relationships: [],
  sample_questions: [],
});

describe("semantic review helpers", () => {
  it("detects overridden fields against the baseline", () => {
    expect(changedFields(col({ description: "Town" }), col(), ["description", "role"])).toEqual([
      "description",
    ]);
  });

  it("builds a diff including added and removed metrics", () => {
    const m = {
      name: "rev",
      display_name: "",
      description: "",
      expression: "SUM(a)",
      filters: [],
      unit: null,
      synonyms: [],
      confidence: null,
      rationale: null,
      provenance: {},
      review_status: "accepted" as const,
    };
    const d = diffModels(model([col()], [m]), model([col({ role: "identifier" })], [{ ...m, name: "rev2" }]));
    expect(d.map((x) => `${x.kind}:${x.name}:${x.field}`)).toEqual([
      "column:city:role",
      "metric:rev2:(added)",
      "metric:rev:(removed)",
    ]);
  });

  it("puts errors and low confidence first", () => {
    const err = [{ path: "columns.city", message: "x", severity: "error" as const }];
    expect(attentionRank(col(), err)).toBe(0);
    expect(attentionRank(col({ confidence: "low" }), [])).toBe(1);
    expect(attentionRank(col({ review_status: "accepted" }), [])).toBe(5);
    expect(needsAttention(col({ review_status: "accepted" }), [])).toBe(false);
  });
});
