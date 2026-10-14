import { useState } from "react";
import { Link } from "react-router-dom";
import { api, ServerInfo } from "../api";
import { Badge, Empty, Err, Field, Method, statusKind, usePoll } from "../components/ui";

export default function Servers() {
  const { data: servers, error, reload } = usePoll(api.servers, 2000);
  const { data: tapes } = usePoll(api.tapes, 10000);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const [rtape, setRtape] = useState("");
  const [rtarget, setRtarget] = useState("http://localhost:8000");
  const [rport, setRport] = useState("9000");
  const [append, setAppend] = useState(false);

  async function record() {
    setErr(null);
    try {
      await api.startRecorder({ tape: rtape.trim(), target: rtarget, port: Number(rport), append });
      await reload();
    } catch (e) { setErr((e as Error).message); }
  }
  async function stop(id: string) {
    try { await api.stopServer(id); await reload(); } catch (e) { setErr((e as Error).message); }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-white">Servers</h1>
        <p className="text-sm text-muted">Fake servers answer from a tape. Recorders sit in front of your real API and save what passes through.</p>
      </div>

      <div className="card space-y-3 p-4">
        <h2 className="font-medium text-white">Start a recorder</h2>
        <div className="grid gap-3 sm:grid-cols-4">
          <Field label="Tape name"><input className="input" value={rtape} onChange={(e) => setRtape(e.target.value)} placeholder="my-session" /></Field>
          <Field label="Your API"><input className="input" value={rtarget} onChange={(e) => setRtarget(e.target.value)} /></Field>
          <Field label="Recorder port"><input className="input" value={rport} onChange={(e) => setRport(e.target.value)} /></Field>
          <label className="flex items-end gap-2 pb-2 text-sm text-muted"><input type="checkbox" checked={append} onChange={(e) => setAppend(e.target.checked)} /> add to existing tape</label>
        </div>
        <p className="text-xs text-muted">Then point your app or browser at the recorder's port instead of your API.</p>
        <button className="btn btn-primary" onClick={record} disabled={!rtape.trim()}>Start recording</button>
      </div>

      <Err error={err ?? error} />
      {servers && servers.length === 0 && (
        <Empty>
          Nothing is running. Start a recorder above, or open a tape
          {tapes && tapes[0] ? <> like <Link className="text-accent" to={`/tapes/${encodeURIComponent(tapes[0].name)}`}>{tapes[0].name}</Link></> : null} and start a fake server.
        </Empty>
      )}
      <div className="space-y-3">
        {servers?.map((s) => (
          <div key={s.id} className="card p-4">
            <div className="flex flex-wrap items-center gap-3">
              <Badge kind={s.kind === "mock" ? "accent" : "warn"}>{s.kind === "mock" ? "fake server" : "recorder"}</Badge>
              <a href={s.url} target="_blank" rel="noreferrer" className="font-mono text-sm text-accent">{s.url}</a>
              <span className="text-sm text-muted">tape: {s.tape}</span>
              <span className="text-sm text-muted">{counter(s)}</span>
              <div className="ml-auto flex gap-2">
                <button className="btn" onClick={() => setOpen(open === s.id ? null : s.id)}>{open === s.id ? "Hide log" : "Log"}</button>
                <button className="btn btn-danger" onClick={() => stop(s.id)}>Stop</button>
              </div>
            </div>
            {open === s.id && <Log id={s.id} />}
          </div>
        ))}
      </div>
    </div>
  );
}

const counter = (s: ServerInfo) =>
  s.kind === "mock" ? `${s.requests_served ?? 0} requests served` : `${s.stats?.recorded ?? 0} calls recorded`;

function Log({ id }: { id: string }) {
  const { data } = usePoll(() => api.serverLog(id), 1500, [id]);
  if (!data) return null;
  if (!Array.isArray(data)) return <pre className="mt-3 rounded-lg bg-bg p-3 font-mono text-xs">{JSON.stringify(data, null, 2)}</pre>;
  return (
    <div className="mt-3 max-h-64 overflow-auto rounded-lg bg-bg p-2 font-mono text-xs">
      {data.length === 0 && <p className="p-2 text-muted">No requests yet.</p>}
      {[...data].reverse().map((r: any, i: number) => (
        <div key={i} className="flex gap-3 px-2 py-0.5">
          <Method m={r.method} /><span className="flex-1 truncate">{r.path}</span>
          <Badge kind={statusKind(r.status)}>{r.status}</Badge><span className="text-muted">{r.match}</span>
        </div>
      ))}
    </div>
  );
}
