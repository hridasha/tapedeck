export type Tape = {
  name: string; created_at: string; source: string; target_url: string | null; entry_count: number; noise: string[];
};
export type EntryRow = {
  id: number; order: number; method: string; path: string; query: string; status: number; duration_ms: number;
  size: number; content_type: string;
};
export type Body = { size: number; binary: boolean; text: string | null; is_json: boolean; truncated: boolean; base64?: string };
export type EntryDetail = {
  id: number; method: string; path: string; query: string; status: number; duration_ms: number;
  request: { headers: Record<string, string>; body: Body };
  response: { headers: Record<string, string>; body: Body };
};
export type ServerInfo = {
  id: string; kind: "mock" | "record"; tape: string; port: number; url: string; running: boolean;
  options: Record<string, unknown>; requests_served?: number; stats?: Record<string, number>;
};
export type Tick = { t: number; requests: number; errors: number; p50: number | null; p95: number | null; p99: number | null; users: number };
export type Diff = { path: string; kind: string; expected: unknown; actual: unknown; volatile: boolean };
export type ReplayRow = {
  order: number; method: string; path: string; query: string; outcome: "pass" | "fail" | "skipped" | "error";
  status_expected: number; status_actual: number | null; ms_expected: number; ms_actual: number | null;
  reasons: string[]; warnings: string[]; detail: string; diffs: Diff[];
};
export type Job = {
  id: string; kind: "replay" | "load"; tape: string; target: string; status: "running" | "done" | "failed" | "cancelled";
  started: number; finished: number | null; progress: Record<string, number>; error: string | null;
  ticks?: Tick[]; result?: any;
};

async function call<T>(method: string, url: string, body?: unknown, form?: FormData): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: form ?? (body ? JSON.stringify(body) : undefined),
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const d = (await res.json()).detail;
      msg = typeof d === "string" ? d : JSON.stringify(d);
    } catch { /* keep the status text */ }
    throw new Error(msg);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

const enc = encodeURIComponent;

export const api = {
  tapes: () => call<Tape[]>("GET", "/api/tapes"),
  importHar: (file: File, name: string, keepStatic: boolean) => {
    const f = new FormData();
    f.append("file", file);
    if (name) f.append("name", name);
    f.append("keep_static", String(keepStatic));
    return call<Tape>("POST", "/api/tapes/import-har", undefined, f);
  },
  deleteTape: (name: string) => call<void>("DELETE", `/api/tapes/${enc(name)}`),
  entries: (name: string) => call<EntryRow[]>("GET", `/api/tapes/${enc(name)}/entries`),
  entry: (name: string, id: number) => call<EntryDetail>("GET", `/api/tapes/${enc(name)}/entries/${id}`),
  noise: (name: string) => call<{ saved: string[]; candidates: string[] }>("GET", `/api/tapes/${enc(name)}/noise`),
  saveNoise: (name: string, paths: string[]) => call<{ saved: string[] }>("PUT", `/api/tapes/${enc(name)}/noise`, { paths }),
  servers: () => call<ServerInfo[]>("GET", "/api/servers"),
  startMock: (b: object) => call<ServerInfo>("POST", "/api/servers/mock", b),
  startRecorder: (b: object) => call<ServerInfo>("POST", "/api/servers/record", b),
  stopServer: (id: string) => call<void>("DELETE", `/api/servers/${enc(id)}`),
  serverLog: (id: string) => call<any>("GET", `/api/servers/${enc(id)}/log`),
  startReplay: (b: object) => call<Job>("POST", "/api/replays", b),
  startLoad: (b: object) => call<Job>("POST", "/api/loads", b),
  jobs: () => call<Job[]>("GET", "/api/jobs"),
  job: (id: string) => call<Job>("GET", `/api/jobs/${id}`),
  cancelJob: (id: string) => call<void>("DELETE", `/api/jobs/${id}`),
};

export const fmtMs = (v: number | null | undefined) => (v == null ? "–" : v >= 100 ? `${Math.round(v)} ms` : `${v.toFixed(1)} ms`);
export const fmtBytes = (n: number) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);
export const fmtTime = (t: number | string) => new Date(typeof t === "number" ? t * 1000 : t).toLocaleString();
