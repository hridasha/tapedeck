import { Link } from "react-router-dom";
import { api, fmtTime } from "../api";
import { Badge, Empty, Err, usePoll } from "../components/ui";

export const jobKind = (s: string) => (s === "done" ? "ok" : s === "running" ? "accent" : s === "failed" ? "bad" : "mute");

export default function Runs() {
  const { data: jobs, error } = usePoll(api.jobs, 2000);
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold text-white">Runs</h1>
        <p className="text-sm text-muted">Replays and load tests started from a tape. They are kept until the app restarts.</p>
      </div>
      <Err error={error} />
      {jobs && jobs.length === 0 && <Empty>No runs yet. Open a tape and choose Replay or Load test.</Empty>}
      <div className="space-y-2">
        {jobs?.map((j) => (
          <Link key={j.id} to={`/runs/${j.id}`} className="card flex flex-wrap items-center gap-3 p-3 transition hover:border-accent">
            <Badge kind={j.kind === "load" ? "warn" : "accent"}>{j.kind}</Badge>
            <span className="font-medium text-white">{j.tape}</span>
            <span className="font-mono text-xs text-muted">{j.target}</span>
            <span className="ml-auto text-xs text-muted">{fmtTime(j.started)}</span>
            <Badge kind={jobKind(j.status)}>{j.status}</Badge>
          </Link>
        ))}
      </div>
    </div>
  );
}
