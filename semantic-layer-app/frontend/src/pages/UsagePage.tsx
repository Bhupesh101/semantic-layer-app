import { useEffect, useState } from "react";
import { Badge, Button, Card, EmptyState, Modal, PageHeader, Spinner } from "../components/ui";
import { api, timeAgo } from "../lib/api";

interface Usage {
  calls: {
    id: string;
    purpose: string;
    provider: string;
    model: string;
    tokens_in: number | null;
    tokens_out: number | null;
    cost_usd: number | null;
    latency_ms: number | null;
    error: string | null;
    has_payload: boolean;
    created_at: string;
  }[];
  totals: {
    provider: string;
    model: string;
    calls: number;
    tokens_in: number;
    tokens_out: number;
    cost_usd: number | null;
    avg_latency_ms: number | null;
    errors: number;
  }[];
}

export default function UsagePage() {
  const [u, setU] = useState<Usage | null>(null);
  const [payload, setPayload] = useState<any>(null);
  useEffect(() => {
    api.get<Usage>("/api/usage?limit=200").then(setU);
  }, []);
  if (!u) return <Spinner />;
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <PageHeader
        title="LLM usage"
        subtitle="Every LLM call: tokens, estimated cost (OpenRouter pricing), latency and errors."
      />
      {u.totals.length === 0 ? (
        <EmptyState title="No LLM calls yet" />
      ) : (
        <Card className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs text-slate-500 dark:bg-slate-800">
              <tr>
                <th className="px-3 py-2">Provider / model</th>
                <th className="px-3 py-2 text-right">Calls</th>
                <th className="px-3 py-2 text-right">Tokens in</th>
                <th className="px-3 py-2 text-right">Tokens out</th>
                <th className="px-3 py-2 text-right">Est. cost (USD)</th>
                <th className="px-3 py-2 text-right">Avg latency</th>
                <th className="px-3 py-2 text-right">Errors</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {u.totals.map((t) => (
                <tr key={t.provider + t.model}>
                  <td className="px-3 py-2">
                    {t.provider} / <span className="font-mono text-xs">{t.model}</span>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{t.calls}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{t.tokens_in.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{t.tokens_out.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {t.cost_usd != null ? `$${t.cost_usd.toFixed(4)}` : "—"}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {t.avg_latency_ms != null ? `${t.avg_latency_ms} ms` : "—"}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{t.errors}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      {u.calls.length > 0 && (
        <Card className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs text-slate-500 dark:bg-slate-800">
              <tr>
                <th className="px-3 py-2">When</th>
                <th className="px-3 py-2">Purpose</th>
                <th className="px-3 py-2">Model</th>
                <th className="px-3 py-2 text-right">Tokens</th>
                <th className="px-3 py-2 text-right">Latency</th>
                <th className="px-3 py-2">Result</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {u.calls.map((c) => (
                <tr key={c.id}>
                  <td className="whitespace-nowrap px-3 py-2 text-slate-500">{timeAgo(c.created_at)}</td>
                  <td className="px-3 py-2">
                    <Badge>{c.purpose}</Badge>
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{c.model}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {c.tokens_in ?? "—"} / {c.tokens_out ?? "—"}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{c.latency_ms} ms</td>
                  <td className="max-w-sm truncate px-3 py-2 text-xs">
                    {c.error ? <span className="text-rose-600">{c.error}</span> : "ok"}
                  </td>
                  <td className="px-3 py-2">
                    {c.has_payload && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          api
                            .get<{ payload: any }>(`/api/usage/${c.id}/payload`)
                            .then((r) => setPayload(r.payload))
                        }
                      >
                        Payload
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      <Modal open={!!payload} onClose={() => setPayload(null)} title="Exact LLM payload" wide>
        <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap text-xs">
          {JSON.stringify(payload, null, 2)}
        </pre>
      </Modal>
    </div>
  );
}
