import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import ChatPanel from "../components/ChatPanel";
import { Alert, Button, Card, EmptyState, Field, Input, Select, cx } from "../components/ui";
import { api, errorMessage, timeAgo } from "../lib/api";
import type { ChatSession, Provider, RuntimeSettings, Source, VersionDetail } from "../lib/types";

function NewChat({ onCreated }: { onCreated: (s: ChatSession) => void }) {
  const [sources, setSources] = useState<Source[] | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [sourceId, setSourceId] = useState("");
  const [providerId, setProviderId] = useState("");
  const [model, setModel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    Promise.all([
      api.get<Source[]>("/api/sources?status=onboarded"),
      api.get<Provider[]>("/api/providers"),
      api.get<RuntimeSettings>("/api/settings"),
    ]).then(([s, ps, rt]) => {
      setSources(s);
      setSourceId(s[0]?.id ?? "");
      setProviders(ps);
      // P1-07: chat default, then the global default
      const p = ps.find((x) => x.id === rt.chat_provider_id) ?? ps.find((x) => x.is_default) ?? ps[0];
      if (p) {
        setProviderId(p.id);
        setModel(rt.chat_provider_id === p.id && rt.chat_model ? rt.chat_model : p.default_model);
      }
    });
  }, []);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      onCreated(
        await api.post<ChatSession>("/api/chat/sessions", {
          source_id: sourceId,
          provider_id: providerId,
          model,
        }),
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  if (sources && sources.length === 0) {
    return (
      <EmptyState title="No onboarded data sources">
        <Link className="text-indigo-600 hover:underline" to="/sources/new">
          Upload a CSV
        </Link>{" "}
        and approve its semantic model to start chatting.
      </EmptyState>
    );
  }
  if (sources && providers.length === 0) {
    return (
      <EmptyState title="No LLM provider configured">
        <Link className="text-indigo-600 hover:underline" to="/providers">
          Add a provider
        </Link>{" "}
        first.
      </EmptyState>
    );
  }
  const selected = providers.find((p) => p.id === providerId);
  return (
    <Card className="mx-auto max-w-xl space-y-4 p-6">
      <h2 className="text-lg font-semibold">New chat</h2>
      <Field label="Data source" hint="Only onboarded sources (approved semantic model) are listed.">
        <Select
          value={sourceId}
          onChange={(e) => setSourceId(e.target.value)}
          options={(sources ?? []).map((s) => ({ value: s.id, label: `${s.name} (v${s.active_version})` }))}
        />
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Provider">
          <Select
            value={providerId}
            onChange={(e) => {
              setProviderId(e.target.value);
              setModel(providers.find((p) => p.id === e.target.value)?.default_model ?? "");
            }}
            options={providers.map((p) => ({
              value: p.id,
              label: p.name + (p.is_default ? " (default)" : ""),
            }))}
          />
        </Field>
        <Field label="Model">
          <Input list="chat-models" value={model} onChange={(e) => setModel(e.target.value)} />
          <datalist id="chat-models">
            {selected?.available_models.map((m) => <option key={m.id} value={m.id} />)}
          </datalist>
        </Field>
      </div>
      {selected?.supports_tools === false && (
        <Alert tone="amber">
          This model has no native tool calling. Chat will use a text-based tool protocol, which can be less
          reliable.
        </Alert>
      )}
      {error && <Alert>{error}</Alert>}
      <Button variant="primary" onClick={create} loading={busy} disabled={!sourceId || !providerId || !model}>
        Start chat
      </Button>
    </Card>
  );
}

export default function ChatPage() {
  const { sessionId } = useParams();
  const nav = useNavigate();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [starters, setStarters] = useState<string[]>([]);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [title, setTitle] = useState("");

  const loadSessions = useCallback(() => {
    api.get<ChatSession[]>("/api/chat/sessions").then(setSessions);
  }, []);
  useEffect(loadSessions, [loadSessions]);

  const current = sessions.find((s) => s.id === sessionId);
  useEffect(() => {
    if (!current) return setStarters([]);
    api
      .get<VersionDetail>(`/api/semantic/${current.semantic_version_id}`)
      .then((v) => setStarters(v.model.sample_questions.slice(0, 6)));
  }, [current?.semantic_version_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const rename = async (id: string) => {
    if (title.trim()) await api.patch(`/api/chat/sessions/${id}`, { title });
    setRenaming(null);
    loadSessions();
  };

  const remove = async (id: string) => {
    if (!confirm("Delete this chat?")) return;
    await api.del(`/api/chat/sessions/${id}`);
    if (id === sessionId) nav("/chat");
    loadSessions();
  };

  return (
    <div className="flex h-[calc(100vh-3rem)] gap-4 max-md:h-auto max-md:flex-col">
      <aside className="flex w-full shrink-0 flex-col md:w-64">
        <Button variant="primary" onClick={() => nav("/chat")} className="mb-3">
          + New chat
        </Button>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto max-md:max-h-48">
          {sessions.map((s) => (
            <div
              key={s.id}
              className={cx(
                "group rounded-md px-2.5 py-2 text-sm",
                s.id === sessionId
                  ? "bg-indigo-50 dark:bg-indigo-950"
                  : "hover:bg-slate-100 dark:hover:bg-slate-800",
              )}
            >
              {renaming === s.id ? (
                <Input
                  autoFocus
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  onBlur={() => rename(s.id)}
                  onKeyDown={(e) => e.key === "Enter" && rename(s.id)}
                  className="py-1"
                />
              ) : (
                <Link to={`/chat/${s.id}`} className="block">
                  <div className="truncate font-medium">{s.title}</div>
                  <div className="truncate text-xs text-slate-500">
                    {s.source_name} · {timeAgo(s.updated_at)}
                  </div>
                </Link>
              )}
              <div className="mt-1 hidden gap-2 text-xs text-slate-500 group-hover:flex group-focus-within:flex">
                <button
                  onClick={() => {
                    setRenaming(s.id);
                    setTitle(s.title);
                  }}
                >
                  Rename
                </button>
                <button onClick={() => remove(s.id)}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      </aside>
      <section className="min-h-0 min-w-0 flex-1">
        {sessionId ? (
          <Card className="flex h-full flex-col p-4 max-md:h-[75vh]">
            <ChatPanel key={sessionId} sessionId={sessionId} starters={starters} onActivity={loadSessions} />
          </Card>
        ) : (
          <NewChat
            onCreated={(s) => {
              loadSessions();
              nav(`/chat/${s.id}`);
            }}
          />
        )}
      </section>
    </div>
  );
}
