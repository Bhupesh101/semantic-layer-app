import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import type { ModelInfo, ProviderType } from "../lib/types";
import { Button, Input, Toggle, cx } from "./ui";

interface Props {
  type: ProviderType;
  baseUrl: string;
  apiKey?: string;
  providerId?: string;
  value: string;
  onChange: (v: string) => void;
  initialModels?: ModelInfo[];
  autoLoad?: boolean;
}

/** Model dropdown fed by the provider's /models endpoint, with free-text fallback (P1-03, P1-04). */
export default function ModelPicker({
  type,
  baseUrl,
  apiKey,
  providerId,
  value,
  onChange,
  initialModels,
  autoLoad,
}: Props) {
  const [models, setModels] = useState<ModelInfo[]>(initialModels ?? []);
  const [loading, setLoading] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [toolsOnly, setToolsOnly] = useState(true);
  const [browse, setBrowse] = useState(false);

  const load = async () => {
    if (!baseUrl) return;
    setLoading(true);
    setNote(null);
    try {
      const r = await api.post<{ ok: boolean; models: ModelInfo[]; error?: string }>(
        "/api/providers/probe-models",
        {
          type,
          base_url: baseUrl,
          api_key: apiKey || null,
          provider_id: providerId || null,
        },
      );
      setModels(r.models);
      setNote(
        r.ok
          ? `${r.models.length} models available`
          : `Could not list models (${r.error}). Showing suggestions.`,
      );
    } catch (e: any) {
      setNote(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (autoLoad && type !== "openai_compatible") load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type, autoLoad]);

  const hasMeta = models.some((m) => m.supports_tools !== undefined);
  const filtered = useMemo(
    () =>
      models
        .filter((m) => !hasMeta || !toolsOnly || m.supports_tools)
        .filter((m) => !search || `${m.id} ${m.name ?? ""}`.toLowerCase().includes(search.toLowerCase()))
        .slice(0, 300),
    [models, hasMeta, toolsOnly, search],
  );
  const listId = `models-${type}`;

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <Input
          list={listId}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="model id, e.g. openai/gpt-4o-mini"
        />
        <Button type="button" onClick={load} loading={loading} disabled={!baseUrl}>
          Load
        </Button>
        {hasMeta && (
          <Button type="button" variant="ghost" onClick={() => setBrowse((b) => !b)}>
            {browse ? "Hide" : "Browse"}
          </Button>
        )}
      </div>
      <datalist id={listId}>
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.name}
          </option>
        ))}
      </datalist>
      {note && <p className="text-xs text-slate-500">{note}</p>}
      {browse && hasMeta && (
        <div className="rounded-md ring-1 ring-slate-200 dark:ring-slate-700">
          <div className="flex items-center gap-3 border-b border-slate-200 p-2 dark:border-slate-700">
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search models"
              className="py-1"
            />
            <Toggle checked={toolsOnly} onChange={setToolsOnly} label="Supports tools" />
          </div>
          <div className="max-h-64 overflow-auto">
            <table className="min-w-full text-xs">
              <thead className="sticky top-0 bg-slate-50 dark:bg-slate-800">
                <tr className="text-left text-slate-500">
                  <th className="px-2 py-1">Model</th>
                  <th className="px-2 py-1 text-right">Context</th>
                  <th className="px-2 py-1 text-right">$/M in</th>
                  <th className="px-2 py-1 text-right">$/M out</th>
                  <th className="px-2 py-1">Tools</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((m) => (
                  <tr
                    key={m.id}
                    onClick={() => onChange(m.id)}
                    className={cx(
                      "cursor-pointer hover:bg-indigo-50 dark:hover:bg-indigo-950",
                      m.id === value && "bg-indigo-50 dark:bg-indigo-950",
                    )}
                  >
                    <td className="px-2 py-1 font-mono">{m.id}</td>
                    <td className="px-2 py-1 text-right tabular-nums">
                      {m.context_length?.toLocaleString() ?? "—"}
                    </td>
                    <td className="px-2 py-1 text-right tabular-nums">{m.prompt_price ?? "—"}</td>
                    <td className="px-2 py-1 text-right tabular-nums">{m.completion_price ?? "—"}</td>
                    <td className="px-2 py-1">{m.supports_tools ? "✓" : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
