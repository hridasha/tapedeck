import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, EntryDetail, EntryRow, fmtBytes, fmtMs } from "../api";
import { Badge, Code, Empty, Err, Field, Method, statusKind } from "../components/ui";

type Tab = "calls" | "mock" | "replay" | "load" | "noise";

export default function TapeDetail() {
  const { name = "" } = useParams();
  const [tab, setTab] = useState<Tab>("calls");
  const nav = useNavigate();
  const [err, setErr] = useState<string | null>(null);

  async function remove() {
    if (!window.confirm(`Delete the tape "${name}"? This cannot be undone.`)) return;
    try {
      await api.deleteTape(name);
      nav("/");
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  const tabs: [Tab, string][] = [["calls", "Calls"], ["mock", "Fake server"], ["replay", "Replay"], ["load", "Load test"], ["noise", "Ignored fields"]];
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <h1 className="truncate text-2xl font-semibold text-white">{name}</h1>
        <button className="btn btn-danger" onClick={remove}>Delete tape</button>
      </div>
      <Err error={err} />
      <div className="flex gap-1 border-b border-line">
        {tabs.map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${tab === k ? "border-accent text-white" : "border-transparent text-muted hover:text-white"}`}>
            {label}
          </button>
        ))}
      </div>
      {tab === "calls" && <Calls name={name} />}
      {tab === "mock" && <MockForm name={name} />}
      {tab === "replay" && <ReplayForm name={name} />}
      {tab === "load" && <LoadForm name={name} />}
      {tab === "noise" && <Noise name={name} />}
    </div>
  );
}

function Calls({ name }: { name: string }) {
  const [rows, setRows] = useState<EntryRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<number | null>(null);
  useEffect(() => { api.entries(name).then(setRows).catch((e) => setErr(e.message)); }, [name]);
  const shown = useMemo(() => (rows ?? []).filter((r) => `${r.method} ${r.path}${r.query} ${r.status}`.toLowerCase().includes(q.toLowerCase())), [rows, q]);
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
      <div className="space-y-3">
        <Err error={err} />
        <input className="input" placeholder="Filter by method, path or status" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="card max-h-[34rem] overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-panel"><tr><th>#</th><th>Call</th><th>Status</th><th>Time</th><th>Size</th></tr></thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.id} onClick={() => setSel(r.id)} className={`cursor-pointer border-t border-line hover:bg-white/5 ${sel === r.id ? "bg-white/10" : ""}`}>
                  <td className="text-muted">{r.order}</td>
                  <td><Method m={r.method} /> <span className="font-mono text-xs">{r.path}{r.query && <span className="text-muted">?{r.query}</span>}</span></td>
                  <td><Badge kind={statusKind(r.status)}>{r.status}</Badge></td>
                  <td className="text-muted">{fmtMs(r.duration_ms)}</td>
                  <td className="text-muted">{fmtBytes(r.size)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows && shown.length === 0 && <p className="p-6 text-center text-sm text-muted">No calls match.</p>}
        </div>
      </div>
      {sel != null ? <Detail name={name} id={sel} /> : <Empty>Click a call to see its request and response.</Empty>}
    </div>
  );
}

function Detail({ name, id }: { name: string; id: number }) {
  const [d, setD] = useState<EntryDetail | null>(null);
  useEffect(() => { setD(null); api.entry(name, id).then(setD).catch(() => setD(null)); }, [name, id]);
  if (!d) return <Empty>Loading…</Empty>;
  const hdr = (h: Record<string, string>) => Object.entries(h).map(([k, v]) => `${k}: ${v}`).join("\n") || "(none)";
  const body = (b: EntryDetail["response"]["body"]) => (b.binary ? `(binary, ${fmtBytes(b.size)})` : b.text || "(empty)") + (b.truncated ? "\n… cut short" : "");
  return (
    <div className="card space-y-3 p-4">
      <div className="flex items-center gap-2">
        <Method m={d.method} /><span className="truncate font-mono text-sm">{d.path}</span>
        <Badge kind={statusKind(d.status)}>{d.status}</Badge><span className="text-xs text-muted">{fmtMs(d.duration_ms)}</span>
      </div>
      <div><span className="label">Request headers</span><Code>{hdr(d.request.headers)}</Code></div>
      {d.request.body.size > 0 && <div><span className="label">Request body</span><Code>{body(d.request.body)}</Code></div>}
      <div><span className="label">Response headers</span><Code>{hdr(d.response.headers)}</Code></div>
      <div><span className="label">Response body</span><Code>{body(d.response.body)}</Code></div>
    </div>
  );
}

function MockForm({ name }: { name: string }) {
  const nav = useNavigate();
  const [port, setPort] = useState("9100");
  const [latency, setLatency] = useState("0");
  const [fail, setFail] = useState("0");
  const [err, setErr] = useState<string | null>(null);
  async function go() {
    setErr(null);
    try {
      await api.startMock({ tape: name, port: Number(port), latency, fail_rate: Number(fail) / 100 });
      nav("/servers");
    } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div className="card max-w-xl space-y-4 p-5">
      <p className="text-sm text-muted">Starts a fake copy of this API on your computer that answers from the tape. Point your app at it instead of the real server.</p>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Port"><input className="input" value={port} onChange={(e) => setPort(e.target.value)} /></Field>
        <Field label="Delay" hint='"recorded" or ms'><input className="input" value={latency} onChange={(e) => setLatency(e.target.value)} /></Field>
        <Field label="Failures %" hint="random 503s"><input className="input" value={fail} onChange={(e) => setFail(e.target.value)} /></Field>
      </div>
      <Err error={err} />
      <button className="btn btn-primary" onClick={go}>Start fake server</button>
    </div>
  );
}

function ReplayForm({ name }: { name: string }) {
  const nav = useNavigate();
  const [target, setTarget] = useState("http://localhost:8000");
  const [writes, setWrites] = useState(false);
  const [learn, setLearn] = useState(false);
  const [headers, setHeaders] = useState("");
  const [err, setErr] = useState<string | null>(null);
  async function go() {
    setErr(null);
    try {
      const h: Record<string, string> = {};
      headers.split("\n").map((l) => l.trim()).filter(Boolean).forEach((l) => {
        const i = l.indexOf(":");
        if (i < 1) throw new Error(`Header "${l}" needs the form Name: value`);
        h[l.slice(0, i).trim()] = l.slice(i + 1).trim();
      });
      const job = await api.startReplay({ tape: name, target, allow_writes: writes, learn_noise: learn, headers: h });
      nav(`/runs/${job.id}`);
    } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div className="card max-w-xl space-y-4 p-5">
      <p className="text-sm text-muted">Sends every recorded call to your server and shows which answers changed.</p>
      <Field label="Server to test"><input className="input" value={target} onChange={(e) => setTarget(e.target.value)} /></Field>
      <Field label="Extra headers (one per line)" hint="Recorded passwords and tokens were hidden, so add a fresh Authorization here if needed.">
        <textarea className="input h-20 font-mono text-xs" value={headers} onChange={(e) => setHeaders(e.target.value)} placeholder="Authorization: Bearer ..." />
      </Field>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={writes} onChange={(e) => setWrites(e.target.checked)} /> Also send POST / PUT / PATCH / DELETE (changes data)</label>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={learn} onChange={(e) => setLearn(e.target.checked)} /> Learn noisy fields first (asks GETs twice)</label>
      <Err error={err} />
      <button className="btn btn-primary" onClick={go}>Run replay</button>
    </div>
  );
}

function LoadForm({ name }: { name: string }) {
  const nav = useNavigate();
  const [target, setTarget] = useState("http://localhost:8000");
  const [mode, setMode] = useState<"users" | "rate">("users");
  const [users, setUsers] = useState("10");
  const [rate, setRate] = useState("50");
  const [duration, setDuration] = useState("20");
  const [ramp, setRamp] = useState("0");
  const [err, setErr] = useState<string | null>(null);
  async function go() {
    setErr(null);
    try {
      const body: Record<string, unknown> = { tape: name, target, duration: Number(duration), ramp: Number(ramp), users: Number(users) };
      if (mode === "rate") body.rate = Number(rate);
      const job = await api.startLoad(body);
      nav(`/runs/${job.id}`);
    } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div className="card max-w-xl space-y-4 p-5">
      <p className="text-sm text-muted">Repeats the recorded GET calls many times at once and measures how fast your server answers.</p>
      <Field label="Server to test"><input className="input" value={target} onChange={(e) => setTarget(e.target.value)} /></Field>
      <div className="flex gap-4 text-sm">
        <label className="flex items-center gap-2"><input type="radio" checked={mode === "users"} onChange={() => setMode("users")} /> A number of users</label>
        <label className="flex items-center gap-2"><input type="radio" checked={mode === "rate"} onChange={() => setMode("rate")} /> A fixed requests per second</label>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        {mode === "users"
          ? <Field label="Users"><input className="input" value={users} onChange={(e) => setUsers(e.target.value)} /></Field>
          : <Field label="Requests / second"><input className="input" value={rate} onChange={(e) => setRate(e.target.value)} /></Field>}
        <Field label="Seconds"><input className="input" value={duration} onChange={(e) => setDuration(e.target.value)} /></Field>
        {mode === "users" && <Field label="Ramp-up (s)" hint="slowly add users"><input className="input" value={ramp} onChange={(e) => setRamp(e.target.value)} /></Field>}
      </div>
      <Err error={err} />
      <button className="btn btn-primary" onClick={go}>Start load test</button>
    </div>
  );
}

function Noise({ name }: { name: string }) {
  const [cand, setCand] = useState<string[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.noise(name).then((n) => { setCand([...new Set([...n.saved, ...n.candidates])]); setPicked(new Set(n.saved)); }).catch((e) => setErr(e.message));
  }, [name]);
  const toggle = (p: string) => setPicked((s) => { const n = new Set(s); n.has(p) ? n.delete(p) : n.add(p); return n; });
  async function save() {
    try { await api.saveNoise(name, [...picked]); setMsg(`Saved ${picked.size} ignored field(s).`); } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div className="card max-w-xl space-y-3 p-5">
      <p className="text-sm text-muted">Fields that changed between identical calls (ids, timestamps…). Tick the ones replays should ignore.</p>
      <Err error={err} />
      {cand.length === 0 && <p className="text-sm text-muted">No changing fields were found in this tape.</p>}
      {cand.map((p) => (
        <label key={p} className="flex items-center gap-2 font-mono text-sm"><input type="checkbox" checked={picked.has(p)} onChange={() => toggle(p)} />{p}</label>
      ))}
      {msg && <p className="text-sm text-ok">{msg}</p>}
      <button className="btn btn-primary" onClick={save}>Save</button>
    </div>
  );
}
