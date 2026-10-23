import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import GeneratePanel from "../components/GeneratePanel";
import ProfileTable from "../components/ProfileTable";
import {
  Alert,
  Button,
  Card,
  DataTable,
  Field,
  Input,
  PageHeader,
  Select,
  Spinner,
  Textarea,
  Toggle,
  cx,
} from "../components/ui";
import { api, errorMessage, formatBytes, uploadFile } from "../lib/api";
import type { ParseOptions, Preview, Source, SourceDetail } from "../lib/types";

const STEPS = ["Upload", "Parse & preview", "Profile", "Generate", "Review", "Done"] as const;
type Step = (typeof STEPS)[number];

function stepFor(s: Source): Step {
  switch (s.status) {
    case "uploaded":
    case "failed":
      return "Parse & preview";
    case "profiled":
      return "Profile";
    case "draft_ready":
    case "in_review":
      return "Review";
    default:
      return "Done";
  }
}

function StepIndicator({ current }: { current: Step }) {
  const idx = STEPS.indexOf(current);
  return (
    <ol className="mb-6 flex flex-wrap gap-x-1 gap-y-2 text-xs" aria-label="Onboarding steps">
      {STEPS.map((s, i) => (
        <li key={s} className="flex items-center gap-1" aria-current={i === idx ? "step" : undefined}>
          <span
            className={cx(
              "flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-semibold",
              i < idx
                ? "bg-emerald-500 text-white"
                : i === idx
                  ? "bg-indigo-600 text-white"
                  : "bg-slate-200 text-slate-500 dark:bg-slate-700",
            )}
          >
            {i < idx ? "✓" : i + 1}
          </span>
          <span className={cx(i === idx ? "font-semibold text-slate-900 dark:text-white" : "text-slate-500")}>
            {s}
          </span>
          {i < STEPS.length - 1 && <span className="mx-1 text-slate-300">›</span>}
        </li>
      ))}
    </ol>
  );
}

function UploadStep() {
  const nav = useNavigate();
  const [drag, setDrag] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [pct, setPct] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [maxMb, setMaxMb] = useState(500);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.get<{ max_upload_mb: number }>("/api/settings/limits").then((r) => setMaxMb(r.max_upload_mb));
  }, []);

  const go = async (f: File) => {
    setFile(f);
    setError(null);
    if (f.size > maxMb * 1024 * 1024) {
      setError(`${f.name} is ${formatBytes(f.size)}; the limit is ${maxMb} MB.`);
      return;
    }
    setPct(0);
    try {
      const r = await uploadFile<{ source: Source }>("/api/sources/upload", f, setPct);
      nav(`/sources/${r.source.id}/onboard`, { replace: true });
    } catch (e) {
      setError(errorMessage(e));
      setPct(null);
    }
  };

  return (
    <Card className="p-6">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          const f = e.dataTransfer.files[0];
          if (f) go(f);
        }}
        className={cx(
          "flex flex-col items-center justify-center rounded-lg border-2 border-dashed px-6 py-14 text-center transition",
          drag
            ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-950/40"
            : "border-slate-300 dark:border-slate-700",
        )}
      >
        <p className="text-base font-medium">Drop a CSV file here</p>
        <p className="mt-1 text-sm text-slate-500">or</p>
        <Button
          className="mt-3"
          variant="primary"
          onClick={() => input.current?.click()}
          disabled={pct !== null}
        >
          Choose file
        </Button>
        <input
          ref={input}
          type="file"
          accept=".csv,.tsv,.txt,text/csv"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && go(e.target.files[0])}
        />
        <p className="mt-3 text-xs text-slate-400">
          Max {maxMb} MB · delimiter, header and encoding are detected automatically
        </p>
      </div>
      {file && pct !== null && (
        <div className="mt-4">
          <div className="flex justify-between text-sm">
            <span>
              {file.name} · {formatBytes(file.size)}
            </span>
            <span>{pct < 100 ? `${pct}%` : "Analysing…"}</span>
          </div>
          <div className="mt-1 h-2 overflow-hidden rounded bg-slate-200 dark:bg-slate-700">
            <div className="h-full bg-indigo-600 transition-all" style={{ width: `${pct}%` }} />
          </div>
        </div>
      )}
      {error && (
        <div className="mt-4">
          <Alert>{error}</Alert>
        </div>
      )}
    </Card>
  );
}

const DELIMS = [
  { value: ",", label: "Comma ," },
  { value: ";", label: "Semicolon ;" },
  { value: "\t", label: "Tab" },
  { value: "|", label: "Pipe |" },
];

function ParseStep({ source, onIngested }: { source: Source; onIngested: () => void }) {
  const [opts, setOpts] = useState<ParseOptions>(source.parse_options);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [name, setName] = useState(source.name);
  const [description, setDescription] = useState(source.description);
  const [loading, setLoading] = useState(false);
  const [ingesting, setIngesting] = useState(false);
  const [error, setError] = useState<string | null>(source.error);

  const reparse = useCallback(
    async (o: ParseOptions) => {
      setLoading(true);
      setError(null);
      try {
        setPreview(await api.post<Preview>(`/api/sources/${source.id}/preview`, o));
      } catch (e) {
        setPreview(null);
        setError(errorMessage(e));
      } finally {
        setLoading(false);
      }
    },
    [source.id],
  );

  useEffect(() => {
    reparse(source.parse_options);
  }, [reparse, source.parse_options]);

  const ingest = async () => {
    setIngesting(true);
    setError(null);
    try {
      await api.post(`/api/sources/${source.id}/ingest`, { name, description, options: opts });
      onIngested();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setIngesting(false);
    }
  };

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <h2 className="mb-3 font-semibold">Parse options</h2>
        <div className="grid items-end gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Field label="Delimiter">
            <Select
              value={opts.delimiter ?? ","}
              onChange={(e) => setOpts({ ...opts, delimiter: e.target.value })}
              options={
                DELIMS.some((d) => d.value === opts.delimiter)
                  ? DELIMS
                  : [...DELIMS, { value: opts.delimiter ?? "", label: `"${opts.delimiter}"` }]
              }
            />
          </Field>
          <Field label="Encoding">
            <Select
              value={opts.encoding ?? "utf-8"}
              onChange={(e) => setOpts({ ...opts, encoding: e.target.value as ParseOptions["encoding"] })}
              options={[
                { value: "utf-8", label: "UTF-8" },
                { value: "latin-1", label: "Latin-1" },
              ]}
            />
          </Field>
          <Field label="Quote">
            <Input
              value={opts.quote ?? ""}
              maxLength={1}
              onChange={(e) => setOpts({ ...opts, quote: e.target.value || null })}
              placeholder='"'
            />
          </Field>
          <Field label="Skip rows">
            <Input
              type="number"
              min={0}
              value={opts.skip_rows ?? 0}
              onChange={(e) => setOpts({ ...opts, skip_rows: Number(e.target.value) })}
            />
          </Field>
          <div className="pb-2">
            <Toggle
              checked={opts.header ?? true}
              onChange={(v) => setOpts({ ...opts, header: v })}
              label="Header row"
            />
          </div>
          <Button onClick={() => reparse(opts)} loading={loading}>
            Re-parse
          </Button>
        </div>
      </Card>
      {error && <Alert>{error}</Alert>}
      {preview && (
        <Card className="p-4">
          <h2 className="mb-2 font-semibold">Preview · first {preview.rows.length} rows</h2>
          {preview.warnings.map((w) => (
            <div key={w} className="mb-2">
              <Alert tone="amber">{w}</Alert>
            </div>
          ))}
          {preview.malformed_sample.length > 0 && (
            <details className="mb-2 text-xs">
              <summary className="cursor-pointer text-slate-500">Malformed row samples</summary>
              <ul className="mt-1 space-y-1 font-mono">
                {preview.malformed_sample.map((m) => (
                  <li key={m.line}>
                    line {m.line}: {m.error} — {m.raw}
                  </li>
                ))}
              </ul>
            </details>
          )}
          <DataTable columns={preview.columns.map((c) => `${c.original} · ${c.type}`)} rows={preview.rows} />
          <p className="mt-2 text-xs text-slate-500">
            SQL-safe names: {preview.columns.map((c) => c.name).join(", ")}
          </p>
        </Card>
      )}
      <Card className="space-y-3 p-4">
        <Field label="Data source name">
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field
          label="Description / context notes (optional)"
          hint="Sent to the LLM during generation. Explain business terms, units and quirks."
        >
          <Textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <div className="flex justify-end">
          <Button variant="primary" onClick={ingest} loading={ingesting} disabled={!preview || !name.trim()}>
            {ingesting ? "Converting & profiling…" : "Import & profile"}
          </Button>
        </div>
      </Card>
    </div>
  );
}

export default function OnboardWizard() {
  const { id } = useParams();
  const nav = useNavigate();
  const [detail, setDetail] = useState<SourceDetail | null>(null);
  const [step, setStep] = useState<Step>(id ? "Parse & preview" : "Upload");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!id) return;
    api
      .get<SourceDetail>(`/api/sources/${id}`)
      .then((d) => {
        setDetail(d);
        const s = stepFor(d.source);
        if (s === "Review" && d.source.draft_version_id)
          nav(`/review/${d.source.draft_version_id}`, { replace: true });
        else if (s === "Done") nav(`/sources/${id}`, { replace: true });
        else setStep(s);
      })
      .catch((e) => setError(errorMessage(e)));
  }, [id, nav]);

  useEffect(() => {
    if (id) load();
    else setStep("Upload");
  }, [id, load]);

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title={detail ? `Onboard “${detail.source.name}”` : "New data source"}
        subtitle="CSV only for now."
      />
      <StepIndicator current={step} />
      {error && <Alert>{error}</Alert>}
      {step === "Upload" && <UploadStep />}
      {id && !detail && !error && <Spinner />}
      {detail && step === "Parse & preview" && <ParseStep source={detail.source} onIngested={load} />}
      {detail && step === "Profile" && detail.profile && (
        <div className="space-y-4">
          <Card className="p-4">
            <ProfileTable profile={detail.profile} />
          </Card>
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-slate-500">
              Parse options are fixed after import. To change them, delete the source and upload again.
            </p>
            <Button variant="primary" onClick={() => setStep("Generate")}>
              Next: generate semantic layer →
            </Button>
          </div>
        </div>
      )}
      {detail && step === "Generate" && (
        <Card className="p-5">
          <GeneratePanel sourceId={detail.source.id} onDone={(vid) => nav(`/review/${vid}`)} />
        </Card>
      )}
    </div>
  );
}
