import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, errorMessage, streamSSE, type SSEEvent } from "../lib/api";
import type { ChatMessage, ChatSession, ToolResultUI } from "../lib/types";
import ResultView from "./ResultView";
import { Alert, Badge, Button, Spinner, Textarea, cx } from "./ui";

interface Step {
  id: string;
  name: string;
  args: any;
  ok?: boolean;
  result?: ToolResultUI;
}

interface Turn {
  user?: ChatMessage | { content: string };
  text: string;
  steps: Step[];
  tokensIn: number;
  tokensOut: number;
  latencyMs: number;
}

/** Group stored messages into user turns: the user's question, tool steps, and the final answer text. */
function toTurns(messages: ChatMessage[]): Turn[] {
  const turns: Turn[] = [];
  let cur: Turn | null = null;
  const fresh = (): Turn => ({ text: "", steps: [], tokensIn: 0, tokensOut: 0, latencyMs: 0 });
  for (const m of messages) {
    if (m.role === "user") {
      cur = { ...fresh(), user: m };
      turns.push(cur);
      continue;
    }
    if (!cur) {
      cur = fresh();
      turns.push(cur);
    }
    if (m.role === "assistant") {
      if (m.content) cur.text = cur.text ? `${cur.text}\n\n${m.content}` : m.content;
      for (const c of m.tool_calls) cur.steps.push({ id: c.id, name: c.name, args: c.arguments });
      cur.tokensIn += m.tokens_in ?? 0;
      cur.tokensOut += m.tokens_out ?? 0;
      cur.latencyMs += m.latency_ms ?? 0;
    } else if (m.role === "tool") {
      for (const r of m.tool_results) {
        const step = cur.steps.find((s) => s.id === r.tool_call_id);
        if (step) Object.assign(step, { ok: r.ok, result: r.result });
        else cur.steps.push({ id: r.tool_call_id, name: r.name, args: r.args, ok: r.ok, result: r.result });
      }
    }
  }
  return turns;
}

function HowIGotThis({ steps }: { steps: Step[] }) {
  const [open, setOpen] = useState(false);
  if (!steps.length) return null;
  const ms = steps.reduce((a, s) => a + (s.result?.tool_ms ?? 0), 0);
  return (
    <div className="mt-2 rounded-md bg-slate-50 text-xs ring-1 ring-slate-200 dark:bg-slate-900/60 dark:ring-slate-700">
      <button
        className="flex w-full items-center justify-between px-3 py-1.5 text-left text-slate-600 dark:text-slate-300"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span>
          {open ? "▾" : "▸"} How I got this · {steps.length} tool call{steps.length > 1 ? "s" : ""} · {ms} ms
        </span>
        {steps.some((s) => s.ok === false) && <Badge tone="amber">self-corrected</Badge>}
      </button>
      {open && (
        <ol className="space-y-3 border-t border-slate-200 px-3 py-2 dark:border-slate-700">
          {steps.map((s, i) => (
            <li key={s.id || i}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono font-semibold">
                  {i + 1}. {s.name}
                </span>
                {s.ok === undefined ? (
                  <Spinner className="h-3 w-3" />
                ) : s.ok ? (
                  <Badge tone="green">ok</Badge>
                ) : (
                  <Badge tone="red">error</Badge>
                )}
                {s.result?.execution_ms !== undefined && (
                  <span className="text-slate-500">
                    {s.result.total_rows?.toLocaleString()} row(s) · {s.result.execution_ms} ms
                    {s.result.cached ? " · cached" : ""}
                  </span>
                )}
              </div>
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded bg-white p-2 dark:bg-slate-800">
                {JSON.stringify(s.args, null, 1)}
              </pre>
              {s.result?.error && <p className="mt-1 text-rose-600">{s.result.error}</p>}
              {s.result?.sql && (
                <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded bg-white p-2 font-mono text-[11px] text-indigo-800 dark:bg-slate-800 dark:text-indigo-300">
                  {s.result.sql}
                  {s.result.params?.length ? `\n-- params: ${JSON.stringify(s.result.params)}` : ""}
                </pre>
              )}
              {s.result?.filters_applied && s.result.filters_applied.length > 0 && (
                <p className="mt-1 text-slate-500">Filters: {s.result.filters_applied.join(" · ")}</p>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function AssistantTurn({ turn, streaming }: { turn: Turn; streaming: boolean }) {
  const [copied, setCopied] = useState(false);
  const data = [...turn.steps]
    .reverse()
    .find(
      (s) =>
        s.ok &&
        s.result?.rows &&
        s.result.column_meta?.length &&
        ["query_metrics", "get_rows", "run_sql"].includes(s.name),
    );
  return (
    <div className="max-w-full">
      {turn.text ? (
        <div className="prose-chat text-sm text-slate-800 dark:text-slate-200">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{turn.text}</ReactMarkdown>
        </div>
      ) : streaming ? (
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <Spinner /> {turn.steps.length ? `Running ${turn.steps[turn.steps.length - 1].name}…` : "Thinking…"}
        </div>
      ) : null}
      {data?.result && <ResultView result={data.result} />}
      <HowIGotThis steps={turn.steps} />
      {!streaming && turn.text && (
        <div className="mt-1 flex items-center gap-3 text-xs text-slate-400">
          <button
            className="hover:text-slate-600"
            onClick={() => {
              navigator.clipboard.writeText(turn.text);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
          >
            {copied ? "Copied" : "Copy"}
          </button>
          {turn.latencyMs > 0 && <span>{(turn.latencyMs / 1000).toFixed(1)} s LLM time</span>}
          {turn.tokensIn > 0 && (
            <span>
              {turn.tokensIn.toLocaleString()} in / {turn.tokensOut.toLocaleString()} out tokens
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export default function ChatPanel({
  sessionId,
  starters = [],
  compact,
  onActivity,
}: {
  sessionId: string;
  starters?: string[];
  compact?: boolean;
  onActivity?: () => void;
}) {
  const [session, setSession] = useState<ChatSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [live, setLive] = useState<Turn | null>(null);
  const [input, setInput] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    const s = await api.get<ChatSession & { messages: ChatMessage[] }>(`/api/chat/sessions/${sessionId}`);
    setSession(s);
    setMessages(s.messages);
  }, [sessionId]);

  useEffect(() => {
    setLive(null);
    setWarnings([]);
    setError(null);
    load().catch((e) => setError(errorMessage(e)));
    return () => abort.current?.abort();
  }, [load]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, live]);

  const run = async (path: string, body: unknown, question?: string) => {
    setError(null);
    setWarnings([]);
    const turn: Turn = {
      user: question !== undefined ? { content: question } : undefined,
      text: "",
      steps: [],
      tokensIn: 0,
      tokensOut: 0,
      latencyMs: 0,
    };
    setLive({ ...turn });
    const ctrl = new AbortController();
    abort.current = ctrl;
    const onEvent = (e: SSEEvent) => {
      if (e.type === "token") turn.text += e.text;
      else if (e.type === "tool_call") turn.steps.push({ id: e.id, name: e.name, args: e.arguments });
      else if (e.type === "tool_result") {
        const s = turn.steps.find((x) => x.id === e.id);
        if (s) Object.assign(s, { ok: e.ok, result: e.result });
      } else if (e.type === "assistant_message" && e.has_tool_calls) turn.text = "";
      else if (e.type === "warning") setWarnings((w) => [...w, e.message]);
      else if (e.type === "error") setError(e.message);
      setLive({ ...turn, steps: [...turn.steps] });
    };
    try {
      await streamSSE(path, body, onEvent, ctrl.signal);
    } catch (e: any) {
      if (e.name !== "AbortError") setError(errorMessage(e));
    } finally {
      abort.current = null;
      setLive(null);
      await load();
      onActivity?.();
    }
  };

  const send = (text: string) => {
    const q = text.trim();
    if (!q || live) return;
    setInput("");
    // the stored history already contains everything before this turn
    run(`/api/chat/sessions/${sessionId}/messages`, { content: q }, q);
  };

  const turns = toTurns(messages);
  const lastHasUser = turns.length > 0 && !!turns[turns.length - 1].user;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {session && !compact && (
        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <span>
            {session.source_name} · semantic v{session.semantic_version} · {session.provider_name} /{" "}
            {session.model}
          </span>
          {session.supports_tools === false && <Badge tone="amber">text tool protocol</Badge>}
        </div>
      )}
      {session?.newer_version_available && (
        <div className="mb-2">
          <Alert tone="amber">
            A newer semantic model is active for this source. This chat stays on v{session.semantic_version};
            start a new chat to use the latest.
          </Alert>
        </div>
      )}
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto pr-1">
        {turns.length === 0 && !live && (
          <div className="py-8 text-center">
            <p className="text-sm text-slate-500">
              Ask a question about your data. The model sees only the semantic layer; queries run locally.
            </p>
            {starters.length > 0 && (
              <div className="mx-auto mt-4 flex max-w-2xl flex-wrap justify-center gap-2">
                {starters.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="rounded-full bg-white px-3 py-1.5 text-sm text-slate-700 ring-1 ring-slate-200 hover:bg-indigo-50 hover:ring-indigo-300 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-700"
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className="space-y-2">
            {t.user && (
              <div className="flex justify-end">
                <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-indigo-600 px-3.5 py-2 text-sm text-white">
                  {t.user.content}
                </div>
              </div>
            )}
            {(t.text || t.steps.length > 0) && <AssistantTurn turn={t} streaming={false} />}
          </div>
        ))}
        {live && (
          <div className="space-y-2">
            {live.user && (
              <div className="flex justify-end">
                <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-indigo-600 px-3.5 py-2 text-sm text-white">
                  {live.user.content}
                </div>
              </div>
            )}
            <AssistantTurn turn={live} streaming />
          </div>
        )}
        {warnings.map((w) => (
          <Alert key={w} tone="amber">
            {w}
          </Alert>
        ))}
        {error && <Alert>{error}</Alert>}
        <div ref={bottom} />
      </div>
      <div className="mt-3 border-t border-slate-200 pt-3 dark:border-slate-800">
        <div className="flex gap-2">
          <Textarea
            rows={2}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            placeholder="Ask about your data… (Enter to send, Shift+Enter for a new line)"
            aria-label="Message"
            className="resize-none"
          />
          {live ? (
            <Button variant="danger" onClick={() => abort.current?.abort()}>
              Stop
            </Button>
          ) : (
            <Button variant="primary" onClick={() => send(input)} disabled={!input.trim()}>
              Send
            </Button>
          )}
        </div>
        {!live && turns.length > 0 && (
          <div className={cx("mt-2 flex gap-3 text-xs text-slate-500")}>
            {lastHasUser && (
              <button
                className="hover:text-slate-800 dark:hover:text-slate-200"
                onClick={() => {
                  // the server drops everything after the last question; mirror that locally
                  const lastUser = messages.map((m) => m.role).lastIndexOf("user");
                  setMessages(messages.slice(0, lastUser + 1));
                  run(`/api/chat/sessions/${sessionId}/retry`, {});
                }}
              >
                ↻ Retry last answer
              </button>
            )}
            <a
              className="hover:text-slate-800 dark:hover:text-slate-200"
              href={`/api/chat/sessions/${sessionId}/export`}
            >
              ⇩ Export as Markdown
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
