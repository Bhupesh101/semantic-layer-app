import { useEffect, useRef, useState } from "react";
import { api, errorMessage } from "../lib/api";
import type { Job, Provider, RuntimeSettings } from "../lib/types";
import { Alert, Button, Field, Input, Modal, Select, Spinner, Textarea, Toggle } from "./ui";

interface PayloadPreview {
  sample_values: number;
  strict_privacy: boolean;
  messages: { role: string; content: string }[];
  approx_tokens: number;
}

/** Privacy controls, "what will be sent" preview and the background generation job (P3-01..P3-03, P3-10). */
export default function GeneratePanel({
  sourceId,
  onDone,
}: {
  sourceId: string;
  onDone: (versionId: string) => void;
}) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [providerId, setProviderId] = useState("");
  const [model, setModel] = useState("");
  const [sampleN, setSampleN] = useState(5);
  const [strict, setStrict] = useState(false);
  const [instructions, setInstructions] = useState("");
  const [preview, setPreview] = useState<PayloadPreview | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number>();

  useEffect(() => {
    Promise.all([api.get<Provider[]>("/api/providers"), api.get<RuntimeSettings>("/api/settings")]).then(
      ([ps, rt]) => {
        setProviders(ps);
        const p = ps.find((x) => x.id === rt.generation_provider_id) ?? ps.find((x) => x.is_default) ?? ps[0];
        if (p) {
          setProviderId(p.id);
          setModel(
            rt.generation_provider_id === p.id && rt.generation_model ? rt.generation_model : p.default_model,
          );
        }
        setSampleN(rt.sample_values_per_column);
        setStrict(rt.strict_privacy);
      },
    );
    // resume a running job after a reload
    api.get<Job | null>(`/api/sources/${sourceId}/jobs/latest`).then((j) => {
      if (j && (j.status === "pending" || j.status === "running")) poll(j.id);
    });
    return () => window.clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceId]);

  const body = () => ({
    provider_id: providerId || null,
    model: model || null,
    sample_values: sampleN,
    strict_privacy: strict,
    instructions,
  });

  const poll = (id: string) => {
    api.get<Job>(`/api/jobs/${id}`).then((j) => {
      setJob(j);
      if (j.status === "pending" || j.status === "running")
        timer.current = window.setTimeout(() => poll(id), 1000);
      else if (j.status === "succeeded" && j.result_version_id) onDone(j.result_version_id);
    });
  };

  const start = async () => {
    setError(null);
    try {
      const j = await api.post<Job>(`/api/sources/${sourceId}/semantic/generate`, body());
      setJob(j);
      poll(j.id);
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  const running = job && (job.status === "pending" || job.status === "running");
  const selected = providers.find((p) => p.id === providerId);

  if (!providers.length) {
    return <Alert tone="amber">Add an LLM provider on the Providers page first.</Alert>;
  }

  return (
    <div className="space-y-4">
      <Alert tone="blue">
        The LLM sees only the <strong>profile</strong>: column names, types, statistics, top values and up to{" "}
        {sampleN} sample values per column. PII-flagged columns are always masked. The full dataset is never
        sent.
      </Alert>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Provider">
          <Select
            value={providerId}
            onChange={(e) => {
              setProviderId(e.target.value);
              setModel(providers.find((p) => p.id === e.target.value)?.default_model ?? "");
            }}
            options={providers.map((p) => ({
              value: p.id,
              label: `${p.name}${p.is_default ? " (default)" : ""}`,
            }))}
          />
        </Field>
        <Field label="Model">
          <Input list="gen-models" value={model} onChange={(e) => setModel(e.target.value)} />
          <datalist id="gen-models">
            {selected?.available_models.map((m) => <option key={m.id} value={m.id} />)}
          </datalist>
        </Field>
        <Field
          label={`Sample values per column: ${strict ? 0 : sampleN}`}
          hint="0–20. More samples help the model guess meanings."
        >
          <input
            type="range"
            min={0}
            max={20}
            value={sampleN}
            disabled={strict}
            onChange={(e) => setSampleN(Number(e.target.value))}
            className="w-full accent-indigo-600"
          />
        </Field>
        <div className="pt-6">
          <Toggle
            checked={strict}
            onChange={setStrict}
            label="Strict privacy mode (names, types and stats only)"
          />
        </div>
      </div>
      <Field
        label="Extra instructions (optional)"
        hint="e.g. 'Amounts are in GBP. Invoices starting with C are cancellations.'"
      >
        <Textarea rows={2} value={instructions} onChange={(e) => setInstructions(e.target.value)} />
      </Field>
      {error && <Alert>{error}</Alert>}
      <div className="flex flex-wrap gap-2">
        <Button
          onClick={() =>
            api
              .post<PayloadPreview>(`/api/sources/${sourceId}/semantic/payload-preview`, body())
              .then(setPreview)
          }
        >
          Preview what will be sent
        </Button>
        <Button variant="primary" onClick={start} disabled={!!running || !providerId}>
          Generate semantic layer
        </Button>
      </div>
      {job && (
        <div className="rounded-md bg-slate-50 p-3 text-sm ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700">
          <div className="mb-2 flex items-center justify-between">
            <span className="flex items-center gap-2 font-medium">
              {running && <Spinner />} Generation {job.status}
            </span>
            {running && (
              <Button
                size="sm"
                onClick={() => api.post(`/api/jobs/${job.id}/cancel`).then(() => poll(job.id))}
              >
                Cancel
              </Button>
            )}
          </div>
          <ol className="list-inside list-decimal space-y-0.5 text-xs text-slate-600 dark:text-slate-400">
            {job.progress.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ol>
          {job.error && (
            <div className="mt-2">
              <Alert>{job.error}</Alert>
            </div>
          )}
        </div>
      )}
      <Modal open={!!preview} onClose={() => setPreview(null)} title="What will be sent to the LLM" wide>
        {preview && (
          <div className="space-y-3">
            <p className="text-sm text-slate-500">
              ≈{preview.approx_tokens.toLocaleString()} tokens ·{" "}
              {preview.strict_privacy ? "strict privacy" : `${preview.sample_values} samples/column`}
            </p>
            {preview.messages.map((m, i) => (
              <div key={i}>
                <p className="mb-1 text-xs font-semibold uppercase text-slate-500">{m.role}</p>
                <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded bg-slate-100 p-3 text-xs dark:bg-slate-800">
                  {m.content}
                </pre>
              </div>
            ))}
          </div>
        )}
      </Modal>
    </div>
  );
}
