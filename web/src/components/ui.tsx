import { ReactNode, useEffect, useState } from "react";

export function Badge({ kind, children }: { kind: "ok" | "bad" | "warn" | "mute" | "accent"; children: ReactNode }) {
  const c = {
    ok: "bg-green-500/15 text-ok", bad: "bg-red-500/15 text-bad", warn: "bg-amber-500/15 text-warn",
    mute: "bg-slate-500/15 text-slate-400", accent: "bg-indigo-500/15 text-accent",
  }[kind];
  return <span className={`badge ${c}`}>{children}</span>;
}

export const statusKind = (s: number) => (s >= 500 ? "bad" : s >= 400 ? "warn" : s >= 200 && s < 400 ? "ok" : "mute");

export function Method({ m }: { m: string }) {
  const c: Record<string, string> = { GET: "text-ok", POST: "text-accent", PUT: "text-warn", PATCH: "text-warn", DELETE: "text-bad" };
  return <span className={`font-mono text-xs font-bold ${c[m] ?? "text-slate-400"}`}>{m}</span>;
}

export function Err({ error }: { error: string | null }) {
  return error ? <p className="rounded-lg border border-bad/40 bg-red-500/10 px-3 py-2 text-sm text-bad">{error}</p> : null;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="card px-6 py-10 text-center text-sm text-muted">{children}</div>;
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  );
}

/** Re-runs `fn` every `ms` while the page is open (for lists that change on their own). */
export function usePoll<T>(fn: () => Promise<T>, ms: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const run = () =>
      fn()
        .then((d) => live && (setData(d), setError(null)))
        .catch((e) => live && setError(String(e.message ?? e)));
    run();
    const t = setInterval(run, ms);
    return () => { live = false; clearInterval(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, reload: () => fn().then(setData).catch((e) => setError(String(e.message ?? e))) };
}

export function Code({ children }: { children: ReactNode }) {
  return <pre className="max-h-96 overflow-auto rounded-lg bg-bg p-3 font-mono text-xs leading-relaxed text-slate-300">{children}</pre>;
}
