import { ReactNode, useEffect } from "react";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "ghost" | "danger";
const variants: Record<Variant, string> = {
  primary: "bg-indigo-600 text-white hover:bg-indigo-500 disabled:bg-indigo-300 dark:disabled:bg-indigo-900",
  secondary:
    "bg-white text-slate-700 ring-1 ring-slate-300 hover:bg-slate-50 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-600 dark:hover:bg-slate-700",
  ghost: "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800",
  danger: "bg-rose-600 text-white hover:bg-rose-500 disabled:bg-rose-300",
};

export function Button({
  variant = "secondary",
  size = "md",
  className,
  loading,
  children,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: "sm" | "md";
  loading?: boolean;
}) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || loading}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:cursor-not-allowed disabled:opacity-60",
        size === "sm" ? "px-2.5 py-1 text-xs" : "px-3.5 py-2 text-sm",
        variants[variant],
        className,
      )}
    >
      {loading && <Spinner className="h-3.5 w-3.5" />}
      {children}
    </button>
  );
}

export function Spinner({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg className={cx("animate-spin", className)} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" className="opacity-25" />
      <path d="M4 12a8 8 0 018-8" stroke="currentColor" strokeWidth="4" className="opacity-75" />
    </svg>
  );
}

const inputBase =
  "w-full rounded-md border-0 bg-white px-3 py-2 text-sm text-slate-900 ring-1 ring-inset ring-slate-300 placeholder:text-slate-400 focus:ring-2 focus:ring-inset focus:ring-indigo-500 dark:bg-slate-900 dark:text-slate-100 dark:ring-slate-600";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(inputBase, props.className)} />;
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cx(inputBase, props.className)} />;
}

export function Select({
  options,
  className,
  ...rest
}: React.SelectHTMLAttributes<HTMLSelectElement> & { options: { value: string; label: string }[] }) {
  return (
    <select {...rest} className={cx(inputBase, "pr-8", className)}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-700 dark:text-slate-300">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">{hint}</span>}
    </label>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: ReactNode;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
      />
      {label}
    </label>
  );
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cx(
        "rounded-lg bg-white shadow-sm ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700",
        className,
      )}
    >
      {children}
    </div>
  );
}

const tones = {
  slate: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  green: "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-900/40 dark:text-emerald-300",
  amber: "bg-amber-50 text-amber-800 ring-amber-600/20 dark:bg-amber-900/40 dark:text-amber-300",
  red: "bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-900/40 dark:text-rose-300",
  blue: "bg-sky-50 text-sky-700 ring-sky-600/20 dark:bg-sky-900/40 dark:text-sky-300",
  indigo: "bg-indigo-50 text-indigo-700 ring-indigo-600/20 dark:bg-indigo-900/40 dark:text-indigo-300",
};

export function Badge({ tone = "slate", children }: { tone?: keyof typeof tones; children: ReactNode }) {
  return (
    <span
      className={cx(
        "inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset ring-transparent",
        tones[tone],
      )}
    >
      {children}
    </span>
  );
}

const statusTone: Record<string, keyof typeof tones> = {
  uploaded: "slate",
  profiled: "blue",
  draft_ready: "amber",
  in_review: "amber",
  onboarded: "green",
  failed: "red",
  archived: "slate",
  draft: "amber",
  approved: "green",
  superseded: "slate",
};

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={statusTone[status] ?? "slate"}>{status.replace("_", " ")}</Badge>;
}

export function Alert({
  tone = "red",
  children,
}: {
  tone?: "red" | "amber" | "blue" | "green";
  children: ReactNode;
}) {
  const c = {
    red: "bg-rose-50 text-rose-800 ring-rose-200 dark:bg-rose-950/50 dark:text-rose-200 dark:ring-rose-900",
    amber:
      "bg-amber-50 text-amber-900 ring-amber-200 dark:bg-amber-950/50 dark:text-amber-200 dark:ring-amber-900",
    blue: "bg-sky-50 text-sky-900 ring-sky-200 dark:bg-sky-950/50 dark:text-sky-200 dark:ring-sky-900",
    green:
      "bg-emerald-50 text-emerald-900 ring-emerald-200 dark:bg-emerald-950/50 dark:text-emerald-200 dark:ring-emerald-900",
  }[tone];
  return <div className={cx("rounded-md px-3 py-2 text-sm ring-1", c)}>{children}</div>;
}

export function Modal({
  open,
  onClose,
  title,
  children,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4 pt-[8vh]"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cx(
          "w-full rounded-lg bg-white shadow-xl ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700",
          wide ? "max-w-4xl" : "max-w-lg",
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3 dark:border-slate-700">
          <h2 className="text-base font-semibold">{title}</h2>
          <button
            onClick={onClose}
            className="rounded p-1 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-slate-900 dark:text-white">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 px-6 py-12 text-center dark:border-slate-700">
      <p className="font-medium text-slate-700 dark:text-slate-200">{title}</p>
      {children && <div className="mt-2 text-sm text-slate-500 dark:text-slate-400">{children}</div>}
    </div>
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { id: T; label: ReactNode }[];
  value: T;
  onChange: (t: T) => void;
}) {
  return (
    <div className="mb-4 flex gap-1 border-b border-slate-200 dark:border-slate-700" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={cx(
            "-mb-px border-b-2 px-3 py-2 text-sm font-medium",
            value === t.id
              ? "border-indigo-600 text-indigo-700 dark:text-indigo-300"
              : "border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function DataTable({
  columns,
  rows,
  maxHeight = "24rem",
}: {
  columns: string[];
  rows: any[][];
  maxHeight?: string;
}) {
  return (
    <div className="overflow-auto rounded-md ring-1 ring-slate-200 dark:ring-slate-700" style={{ maxHeight }}>
      <table className="min-w-full text-xs">
        <thead className="sticky top-0 bg-slate-50 dark:bg-slate-800">
          <tr>
            {columns.map((c) => (
              <th
                key={c}
                className="whitespace-nowrap px-2.5 py-1.5 text-left font-semibold text-slate-600 dark:text-slate-300"
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
          {rows.map((r, i) => (
            <tr key={i} className="hover:bg-slate-50 dark:hover:bg-slate-800/50">
              {r.map((v, j) => (
                <td
                  key={j}
                  className={cx(
                    "whitespace-nowrap px-2.5 py-1 text-slate-700 dark:text-slate-300",
                    typeof v === "number" && "text-right tabular-nums",
                  )}
                >
                  {v === null ? (
                    <span className="text-slate-400">null</span>
                  ) : typeof v === "number" ? (
                    v.toLocaleString(undefined, { maximumFractionDigits: 4 })
                  ) : (
                    String(v)
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
