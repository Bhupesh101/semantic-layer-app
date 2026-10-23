import { useCallback, useEffect, useState } from "react";
import ModelPicker from "../components/ModelPicker";
import {
  Alert,
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Modal,
  PageHeader,
  Select,
} from "../components/ui";
import { api, errorMessage, timeAgo } from "../lib/api";
import type { Provider, ProviderDefaults, ProviderType } from "../lib/types";

interface FormState {
  name: string;
  type: ProviderType;
  base_url: string;
  api_key: string;
  default_model: string;
  temperature: string;
  max_tokens: string;
  timeout_s: string;
  retries: string;
  http_referer: string;
  x_title: string;
}

function emptyForm(type: ProviderType, d: ProviderDefaults): FormState {
  const t = d[type] ?? {};
  return {
    name: t.label ?? type,
    type,
    base_url: t.base_url ?? "",
    api_key: "",
    default_model: t.default_model ?? "",
    temperature: "",
    max_tokens: "",
    timeout_s: "",
    retries: "",
    http_referer: t.headers?.http_referer ?? "",
    x_title: t.headers?.x_title ?? "",
  };
}

function fromProvider(p: Provider): FormState {
  const s = (v: unknown) => (v === undefined || v === null ? "" : String(v));
  return {
    name: p.name,
    type: p.type,
    base_url: p.base_url,
    api_key: "",
    default_model: p.default_model,
    temperature: s(p.params.temperature),
    max_tokens: s(p.params.max_tokens),
    timeout_s: s(p.params.timeout_s),
    retries: s(p.params.retries),
    http_referer: s(p.extra_headers.http_referer),
    x_title: s(p.extra_headers.x_title),
  };
}

function ProviderForm({
  defaults,
  editing,
  onSaved,
  onCancel,
}: {
  defaults: ProviderDefaults;
  editing: Provider | null;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [f, setF] = useState<FormState>(editing ? fromProvider(editing) : emptyForm("openrouter", defaults));
  const [advanced, setAdvanced] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof FormState) => (v: string) => setF((s) => ({ ...s, [k]: v }));
  const t = defaults[f.type] ?? {};
  const num = (v: string) => (v.trim() === "" ? undefined : Number(v));

  const save = async () => {
    setSaving(true);
    setError(null);
    const params = {
      temperature: num(f.temperature),
      max_tokens: num(f.max_tokens),
      timeout_s: num(f.timeout_s),
      retries: num(f.retries),
    };
    const extra_headers = { http_referer: f.http_referer || undefined, x_title: f.x_title || undefined };
    try {
      if (editing) {
        await api.patch(`/api/providers/${editing.id}`, {
          name: f.name,
          base_url: f.base_url,
          api_key: f.api_key || undefined,
          default_model: f.default_model,
          params,
          extra_headers,
        });
      } else {
        await api.post("/api/providers", { ...f, api_key: f.api_key || undefined, params, extra_headers });
      }
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      {!editing && (
        <Field label="Type">
          <Select
            value={f.type}
            onChange={(e) => setF(emptyForm(e.target.value as ProviderType, defaults))}
            options={[
              { value: "openrouter", label: "OpenRouter (default)" },
              { value: "ollama", label: "Ollama (local)" },
              { value: "openai_compatible", label: "OpenAI-compatible (LM Studio, vLLM, …)" },
            ]}
          />
        </Field>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name">
          <Input value={f.name} onChange={(e) => set("name")(e.target.value)} />
        </Field>
        <Field label="Base URL">
          <Input
            value={f.base_url}
            onChange={(e) => set("base_url")(e.target.value)}
            placeholder="https://host/v1"
          />
        </Field>
      </div>
      <Field
        label={`API key${t.api_key_required ? "" : " (optional)"}`}
        hint={
          editing?.has_api_key
            ? `Stored: ${editing.api_key_masked}. Leave empty to keep it.`
            : "Encrypted at rest; never shown again in full."
        }
      >
        <Input
          type="password"
          autoComplete="off"
          value={f.api_key}
          onChange={(e) => set("api_key")(e.target.value)}
          placeholder={t.api_key_placeholder ?? ""}
        />
      </Field>
      <Field label="Default model">
        <ModelPicker
          type={f.type}
          baseUrl={f.base_url}
          apiKey={f.api_key}
          providerId={editing?.id}
          value={f.default_model}
          onChange={set("default_model")}
          initialModels={
            editing?.available_models?.length
              ? editing.available_models
              : (t.suggested_models ?? []).map((id: string) => ({ id }))
          }
        />
      </Field>
      <button
        type="button"
        className="text-sm text-indigo-600 hover:underline"
        onClick={() => setAdvanced((a) => !a)}
      >
        {advanced ? "Hide" : "Show"} generation parameters{f.type === "openrouter" ? " & headers" : ""}
      </button>
      {advanced && (
        <div className="grid gap-3 sm:grid-cols-4">
          {(["temperature", "max_tokens", "timeout_s", "retries"] as const).map((k) => (
            <Field key={k} label={k} hint={`default ${defaults.params?.[k]}`}>
              <Input type="number" value={f[k]} onChange={(e) => set(k)(e.target.value)} step="any" />
            </Field>
          ))}
          {f.type === "openrouter" && (
            <>
              <div className="sm:col-span-2">
                <Field label="HTTP-Referer">
                  <Input value={f.http_referer} onChange={(e) => set("http_referer")(e.target.value)} />
                </Field>
              </div>
              <div className="sm:col-span-2">
                <Field label="X-Title">
                  <Input value={f.x_title} onChange={(e) => set("x_title")(e.target.value)} />
                </Field>
              </div>
            </>
          )}
        </div>
      )}
      {error && <Alert>{error}</Alert>}
      <div className="flex justify-end gap-2">
        <Button onClick={onCancel}>Cancel</Button>
        <Button variant="primary" onClick={save} loading={saving}>
          {editing ? "Save" : "Add provider"}
        </Button>
      </div>
    </div>
  );
}

type TestResult = {
  ok: boolean;
  latency_ms: number;
  error?: string;
  reply?: string;
  supports_tools?: boolean;
  model: string;
};

function ProviderCard({ p, onChange, onEdit }: { p: Provider; onChange: () => void; onEdit: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<{ kind: string; r: TestResult } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async (kind: "test" | "test-tools") => {
    setBusy(kind);
    setResult(null);
    try {
      const r = await api.post<TestResult>(`/api/providers/${p.id}/${kind}`, {});
      setResult({ kind, r });
      if (kind === "test-tools") onChange();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    if (!confirm(`Delete provider "${p.name}"?`)) return;
    try {
      await api.del(`/api/providers/${p.id}`);
      onChange();
    } catch (e: any) {
      if (e.status === 409 && e.detail?.in_use && confirm(`${errorMessage(e)}. Delete anyway?`)) {
        await api.del(`/api/providers/${p.id}?force=true`);
        onChange();
      } else setError(errorMessage(e));
    }
  };

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-semibold">{p.name}</h3>
            <Badge tone="indigo">{p.type}</Badge>
            {p.is_default && <Badge tone="green">default</Badge>}
            {p.supports_tools === true && <Badge tone="green">tools ✓</Badge>}
            {p.supports_tools === false && <Badge tone="amber">no native tools</Badge>}
            {p.supports_tools === null && <Badge>tools untested</Badge>}
          </div>
          <dl className="mt-2 grid grid-cols-[auto,1fr] gap-x-3 gap-y-0.5 text-sm text-slate-600 dark:text-slate-400">
            <dt>Model</dt>
            <dd className="truncate font-mono text-xs leading-5">{p.default_model || "—"}</dd>
            <dt>URL</dt>
            <dd className="truncate font-mono text-xs leading-5">{p.base_url}</dd>
            <dt>Key</dt>
            <dd className="font-mono text-xs leading-5">{p.api_key_masked ?? "none"}</dd>
          </dl>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Button size="sm" onClick={() => run("test")} loading={busy === "test"}>
            Test connection
          </Button>
          <Button size="sm" onClick={() => run("test-tools")} loading={busy === "test-tools"}>
            Test tool calling
          </Button>
          {!p.is_default && (
            <Button size="sm" onClick={() => api.post(`/api/providers/${p.id}/default`).then(onChange)}>
              Make default
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={onEdit}>
            Edit
          </Button>
          <Button size="sm" variant="ghost" onClick={remove}>
            Delete
          </Button>
        </div>
      </div>
      {result && (
        <div className="mt-3">
          <Alert
            tone={
              result.r.ok
                ? result.kind === "test-tools" && !result.r.supports_tools
                  ? "amber"
                  : "green"
                : "red"
            }
          >
            {result.r.ok
              ? result.kind === "test"
                ? `Connected in ${result.r.latency_ms} ms. ${result.r.model} replied “${result.r.reply}”.`
                : result.r.supports_tools
                  ? `${result.r.model} supports native tool calling (${result.r.latency_ms} ms).`
                  : `${result.r.model} did not make a native tool call. Chat will fall back to a text protocol.`
              : `Failed after ${result.r.latency_ms} ms: ${result.r.error}`}
          </Alert>
        </div>
      )}
      {error && (
        <div className="mt-3">
          <Alert>{error}</Alert>
        </div>
      )}
      <p className="mt-2 text-xs text-slate-400">Added {timeAgo(p.created_at)}</p>
    </Card>
  );
}

export default function ProvidersPage() {
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [defaults, setDefaults] = useState<ProviderDefaults | null>(null);
  const [modal, setModal] = useState<{ editing: Provider | null } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .get<Provider[]>("/api/providers")
      .then(setProviders)
      .catch((e) => setError(errorMessage(e)));
  }, []);
  useEffect(() => {
    load();
    api.get<ProviderDefaults>("/api/providers/defaults").then(setDefaults);
  }, [load]);

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        title="LLM providers"
        subtitle="OpenRouter, Ollama or any OpenAI-compatible endpoint. Defaults are pre-filled from provider_defaults.yaml."
        actions={
          <Button variant="primary" onClick={() => setModal({ editing: null })} disabled={!defaults}>
            Add provider
          </Button>
        }
      />
      {error && <Alert>{error}</Alert>}
      {providers && providers.length === 0 && (
        <EmptyState title="No providers yet">
          Add OpenRouter (paste an API key) or Ollama (no changes needed if it runs locally).
        </EmptyState>
      )}
      <div className="space-y-3">
        {providers?.map((p) => (
          <ProviderCard key={p.id} p={p} onChange={load} onEdit={() => setModal({ editing: p })} />
        ))}
      </div>
      <Modal
        open={!!modal}
        onClose={() => setModal(null)}
        title={modal?.editing ? `Edit ${modal.editing.name}` : "Add provider"}
        wide
      >
        {modal && defaults && (
          <ProviderForm
            defaults={defaults}
            editing={modal.editing}
            onCancel={() => setModal(null)}
            onSaved={() => {
              setModal(null);
              load();
            }}
          />
        )}
      </Modal>
    </div>
  );
}
