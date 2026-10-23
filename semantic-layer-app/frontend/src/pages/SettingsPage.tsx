import { useEffect, useState } from "react";
import { Alert, Button, Card, Field, Input, PageHeader, Select, Spinner, Toggle } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import type { Provider, RuntimeSettings } from "../lib/types";

function NumberField({
  label,
  hint,
  value,
  onChange,
  step = 1,
}: {
  label: string;
  hint?: string;
  value: number;
  onChange: (v: number) => void;
  step?: number;
}) {
  return (
    <Field label={label} hint={hint}>
      <Input type="number" step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </Field>
  );
}

function PurposeDefault({
  label,
  providers,
  providerId,
  model,
  onChange,
}: {
  label: string;
  providers: Provider[];
  providerId: string | null;
  model: string | null;
  onChange: (pid: string | null, model: string | null) => void;
}) {
  const p = providers.find((x) => x.id === providerId);
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Field label={`${label}: provider`}>
        <Select
          value={providerId ?? ""}
          onChange={(e) => onChange(e.target.value || null, null)}
          options={[
            { value: "", label: "Use the global default" },
            ...providers.map((x) => ({ value: x.id, label: x.name })),
          ]}
        />
      </Field>
      <Field label={`${label}: model`} hint={p ? `Empty = ${p.default_model}` : undefined}>
        <Input
          list={`m-${label}`}
          value={model ?? ""}
          disabled={!providerId}
          onChange={(e) => onChange(providerId, e.target.value || null)}
        />
        <datalist id={`m-${label}`}>
          {p?.available_models.map((m) => <option key={m.id} value={m.id} />)}
        </datalist>
      </Field>
    </div>
  );
}

export default function SettingsPage() {
  const [s, setS] = useState<RuntimeSettings | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    api.get<RuntimeSettings>("/api/settings").then(setS);
    api.get<Provider[]>("/api/providers").then(setProviders);
  }, []);

  if (!s) return <Spinner />;
  const set = (patch: Partial<RuntimeSettings>) => setS({ ...s, ...patch });
  const save = async () => {
    setSaving(true);
    try {
      setS(await api.put<RuntimeSettings>("/api/settings", s));
      setMsg({ ok: true, text: "Settings saved." });
    } catch (e) {
      setMsg({ ok: false, text: errorMessage(e) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <PageHeader
        title="Settings"
        subtitle="Limits and policies. Changes apply to new requests immediately."
        actions={
          <Button variant="primary" onClick={save} loading={saving}>
            Save
          </Button>
        }
      />
      {msg && <Alert tone={msg.ok ? "green" : "red"}>{msg.text}</Alert>}

      <Card className="space-y-4 p-5">
        <h2 className="font-semibold">Default models</h2>
        <p className="text-sm text-slate-500">
          Separate defaults for semantic generation and chat. Otherwise the provider marked default is used.
        </p>
        <PurposeDefault
          label="Generation"
          providers={providers}
          providerId={s.generation_provider_id}
          model={s.generation_model}
          onChange={(pid, m) => set({ generation_provider_id: pid, generation_model: m })}
        />
        <PurposeDefault
          label="Chat"
          providers={providers}
          providerId={s.chat_provider_id}
          model={s.chat_model}
          onChange={(pid, m) => set({ chat_provider_id: pid, chat_model: m })}
        />
      </Card>

      <Card className="space-y-4 p-5">
        <h2 className="font-semibold">Privacy: what the LLM may see</h2>
        <Alert tone="blue">
          The full dataset is never sent to an LLM. Generation sees the profile; chat sees the semantic model
          and capped tool results (aggregates or small row sets).
        </Alert>
        <div className="grid gap-4 sm:grid-cols-2">
          <NumberField
            label="Sample values per column (0–20)"
            value={s.sample_values_per_column}
            onChange={(v) => set({ sample_values_per_column: v })}
          />
          <NumberField
            label="Profile sample size (rows)"
            hint="Profiling uses a sample above this; row counts stay exact."
            value={s.profile_sample_rows}
            onChange={(v) => set({ profile_sample_rows: v })}
          />
        </div>
        <Toggle
          checked={s.strict_privacy}
          onChange={(v) => set({ strict_privacy: v })}
          label="Strict privacy mode by default (no sample values during generation)"
        />
        <Toggle
          checked={s.aggregates_only}
          onChange={(v) => set({ aggregates_only: v })}
          label="Aggregates only (disables the get_rows tool)"
        />
        <Toggle
          checked={s.raw_sql_enabled}
          onChange={(v) => set({ raw_sql_enabled: v })}
          label="Allow the guarded raw SQL tool (SELECT-only, off by default)"
        />
      </Card>

      <Card className="space-y-4 p-5">
        <h2 className="font-semibold">Query & agent limits</h2>
        <div className="grid gap-4 sm:grid-cols-3">
          <NumberField
            label="Rows returned to the LLM"
            value={s.row_cap}
            onChange={(v) => set({ row_cap: v })}
          />
          <NumberField
            label="get_rows max (≤ 50)"
            value={s.get_rows_max}
            onChange={(v) => set({ get_rows_max: v })}
          />
          <NumberField
            label="Query timeout (s)"
            value={s.query_timeout_s}
            step={0.5}
            onChange={(v) => set({ query_timeout_s: v })}
          />
          <NumberField
            label="Max tool iterations"
            value={s.max_agent_iterations}
            onChange={(v) => set({ max_agent_iterations: v })}
          />
          <NumberField
            label="Agent timeout (s)"
            value={s.agent_timeout_s}
            onChange={(v) => set({ agent_timeout_s: v })}
          />
          <NumberField
            label="Context budget (tokens)"
            value={s.context_budget_tokens}
            onChange={(v) => set({ context_budget_tokens: v })}
          />
        </div>
      </Card>

      <Card className="space-y-3 p-5">
        <h2 className="font-semibold">Debugging</h2>
        <Toggle
          checked={s.debug_payloads}
          onChange={(v) => set({ debug_payloads: v })}
          label="Record exact LLM payloads in the usage log (API keys are never included)"
        />
      </Card>
    </div>
  );
}
