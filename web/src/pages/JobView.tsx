import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, Diff, Job, ReplayRow, fmtMs } from "../api";
import { LineChart } from "../components/Chart";
import { Badge, Err, Method } from "../components/ui";
import { jobKind } from "./Runs";

/** Follows a job live over a WebSocket; falls back to one plain fetch if the socket closes. */
function useJob(id: string) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let closed = false;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/jobs/${id}`);
    ws.onmessage = (m) => setJob(JSON.parse(m.data));
    ws.onclose = () => { if (!closed) api.job(id).then(setJob).catch((e) => setError(e.message)); };
    return () => { closed = true; ws.close(); };
  }, [id]);
  return { job, error, setJob };
}

export default function JobView() {
  const { id = "" } = useParams();
  const { job, error, setJob } = useJob(id);
  if (error) return <Err error={error} />;
  if (!job) return <p className="text-muted">Loading…</p>;

  const running = job.status === "running";
  const pct = job.kind === "load"
    ? Math.min(100, ((job.progress.elapsed ?? 0) / (job.progress.duration || 1)) * 100)
    : Math.min(100, ((job.progress.done ?? 0) / (job.progress.total || 1)) * 100);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/runs" className="text-sm text-muted hover:text-white">← Runs</Link>
        <h1 className="text-2xl font-semibold text-white">{job.kind === "load" ? "Load test" : "Replay"} · {job.tape}</h1>
        <Badge kind={jobKind(job.status)}>{job.status}</Badge>
        <span className="font-mono text-xs text-muted">{job.target}</span>
        <div className="ml-auto flex gap-2">
          {running && <button className="btn btn-danger" onClick={() => api.cancelJob(job.id).then(() => api.job(job.id)).then(setJob)}>Cancel</button>}
          {job.status === "done" && <a className="btn" href={`/api/jobs/${job.id}/report.html`} target="_blank" rel="noreferrer">Open report</a>}
        </div>
      </div>
      {running && (
        <div className="h-2 overflow-hidden rounded-full bg-line"><div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} /></div>
      )}
      <Err error={job.error} />
      {job.kind === "load" ? <Load job={job} /> : <Replay job={job} />}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return <div className="card min-w-28 px-4 py-3"><span className="label">{label}</span><span className="text-2xl font-semibold text-white">{value}</span></div>;
}

function Load({ job }: { job: Job }) {
  const ticks = job.ticks ?? [];
  const sum = job.result?.summary;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap gap-3">
        <Stat label="Requests" value={sum?.requests ?? ticks.reduce((a, t) => a + t.requests, 0)} />
        <Stat label="Errors" value={sum?.errors ?? ticks.reduce((a, t) => a + t.errors, 0)} />
        {sum && <><Stat label="Req / s" value={sum.rps.toFixed(1)} /><Stat label="p50" value={fmtMs(sum.p50_ms)} /><Stat label="p95" value={fmtMs(sum.p95_ms)} /><Stat label="p99" value={fmtMs(sum.p99_ms)} /></>}
      </div>
      {job.result?.knee && (
        <p className="rounded-lg border border-warn/40 bg-amber-500/10 px-3 py-2 text-sm text-warn">
          Latency took off around second {job.result.knee.t} (about {job.result.knee.users} users, p95 {fmtMs(job.result.knee.p95_ms)}).
        </p>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        <LineChart title="Response time" unit="ms" length={ticks.length}
          series={[{ name: "p50", color: "#3ecf8e", values: ticks.map((t) => t.p50) }, { name: "p95", color: "#7c8cff", values: ticks.map((t) => t.p95) }, { name: "p99", color: "#ff6b62", values: ticks.map((t) => t.p99) }]} />
        <LineChart title="Throughput" unit="requests / s" length={ticks.length}
          series={[{ name: "requests", color: "#7c8cff", values: ticks.map((t) => t.requests) }, { name: "errors", color: "#ff6b62", values: ticks.map((t) => t.errors) }]} />
      </div>
      {job.result?.endpoints?.length > 0 && (
        <div className="card overflow-auto">
          <table className="w-full text-sm">
            <thead><tr><th>Slowest endpoints</th><th>Requests</th><th>Errors</th><th>p50</th><th>p95</th><th>Max</th></tr></thead>
            <tbody>
              {job.result.endpoints.map((e: any) => (
                <tr key={e.endpoint} className="border-t border-line">
                  <td className="font-mono text-xs">{e.endpoint}</td><td>{e.requests}</td><td>{e.errors}</td>
                  <td>{fmtMs(e.p50_ms)}</td><td>{fmtMs(e.p95_ms)}</td><td>{fmtMs(e.max_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const outcomeKind = (o: string) => (o === "pass" ? "ok" : o === "fail" ? "bad" : o === "error" ? "warn" : "mute");

function Replay({ job }: { job: Job }) {
  const res = job.result;
  const [only, setOnly] = useState(true);
  const rows: ReplayRow[] = res?.results ?? [];
  const shown = only ? rows.filter((r) => r.outcome !== "pass") : rows;
  return (
    <div className="space-y-5">
      {res ? (
        <div className="flex flex-wrap gap-3">
          <Stat label="Passed" value={res.summary.pass} /><Stat label="Changed" value={res.summary.fail} />
          <Stat label="Errors" value={res.summary.error} /><Stat label="Skipped" value={res.summary.skipped} />
        </div>
      ) : <p className="text-sm text-muted">{job.progress.done ?? 0} of {job.progress.total ?? "?"} calls sent…</p>}
      {res && (
        <>
          <label className="flex items-center gap-2 text-sm text-muted"><input type="checkbox" checked={only} onChange={(e) => setOnly(e.target.checked)} /> Only show calls that did not pass</label>
          {shown.length === 0 && <p className="card p-6 text-center text-sm text-ok">{res.ok ? "Everything matches the tape." : "Nothing to show."}</p>}
          <div className="space-y-2">
            {shown.map((r) => (
              <div key={r.order} className="card p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge kind={outcomeKind(r.outcome)}>{r.outcome}</Badge><Method m={r.method} />
                  <span className="font-mono text-xs">{r.path}{r.query && `?${r.query}`}</span>
                  <span className="ml-auto text-xs text-muted">{r.status_expected} → {r.status_actual ?? "–"} · {fmtMs(r.ms_expected)} → {fmtMs(r.ms_actual)}</span>
                </div>
                {[...r.reasons, ...r.warnings].map((t, i) => <p key={i} className="mt-1 text-xs text-warn">{t}</p>)}
                {r.detail && <p className="mt-1 text-xs text-muted">{r.detail}</p>}
                {r.diffs.length > 0 && <DiffTable diffs={r.diffs} />}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function DiffTable({ diffs }: { diffs: Diff[] }) {
  const show = (v: unknown) => (v === undefined ? "(missing)" : JSON.stringify(v));
  return (
    <table className="mt-2 w-full text-xs">
      <thead><tr><th>Field</th><th>Recorded</th><th>Now</th></tr></thead>
      <tbody>
        {diffs.map((d, i) => (
          <tr key={i} className="border-t border-line">
            <td className="font-mono">{d.path}{d.volatile && <span className="ml-2 text-muted">(looks like a timestamp or id)</span>}</td>
            <td className="font-mono text-bad">{show(d.expected)}</td><td className="font-mono text-ok">{show(d.actual)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
