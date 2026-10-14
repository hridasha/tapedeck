import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtTime } from "../api";
import { Badge, Empty, Err, usePoll } from "../components/ui";

export default function Tapes() {
  const { data: tapes, error, reload } = usePoll(api.tapes, 4000);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [keepStatic, setKeepStatic] = useState(false);
  const file = useRef<HTMLInputElement>(null);

  async function upload() {
    const f = file.current?.files?.[0];
    if (!f) return setErr("Choose a .har file first.");
    setBusy(true);
    setErr(null);
    try {
      await api.importHar(f, name.trim(), keepStatic);
      setName("");
      if (file.current) file.current.value = "";
      await reload();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-white">Tapes</h1>
        <p className="text-sm text-muted">A tape is a saved set of API calls. Import one from your browser, or record one with a Server.</p>
      </div>

      <div className="card space-y-3 p-4">
        <h2 className="font-medium text-white">Import a HAR file</h2>
        <p className="text-xs text-muted">In Chrome: DevTools → Network → right-click → "Save all as HAR with content". Passwords and tokens are hidden when it is imported.</p>
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-56 flex-1">
            <span className="label">HAR file</span>
            <input ref={file} type="file" accept=".har,.json" className="input file:mr-3 file:rounded file:border-0 file:bg-line file:px-2 file:py-0.5 file:text-slate-200" />
          </div>
          <div className="w-48">
            <span className="label">Tape name (optional)</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="from the file name" />
          </div>
          <label className="flex items-center gap-2 pb-2 text-sm text-muted">
            <input type="checkbox" checked={keepStatic} onChange={(e) => setKeepStatic(e.target.checked)} /> keep images, CSS and JS
          </label>
          <button className="btn btn-primary" onClick={upload} disabled={busy}>{busy ? "Importing…" : "Import"}</button>
        </div>
        <Err error={err} />
      </div>

      <Err error={error} />
      {tapes && tapes.length === 0 && <Empty>No tapes yet. Import a HAR file above to get started.</Empty>}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {tapes?.map((t) => (
          <Link key={t.name} to={`/tapes/${encodeURIComponent(t.name)}`} className="card block p-4 transition hover:border-accent">
            <div className="flex items-start justify-between gap-2">
              <h3 className="truncate font-medium text-white">{t.name}</h3>
              <Badge kind="accent">{t.source}</Badge>
            </div>
            <p className="mt-2 text-2xl font-semibold text-white">{t.entry_count} <span className="text-sm font-normal text-muted">calls</span></p>
            <p className="mt-1 text-xs text-muted">{fmtTime(t.created_at)}</p>
            {t.noise.length > 0 && <p className="mt-1 text-xs text-muted">{t.noise.length} ignored field(s)</p>}
          </Link>
        ))}
      </div>
    </div>
  );
}
