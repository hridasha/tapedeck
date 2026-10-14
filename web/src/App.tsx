import { NavLink, Route, Routes } from "react-router-dom";
import Tapes from "./pages/Tapes";
import TapeDetail from "./pages/TapeDetail";
import Servers from "./pages/Servers";
import Runs from "./pages/Runs";
import JobView from "./pages/JobView";

const link = ({ isActive }: { isActive: boolean }) =>
  `rounded-lg px-3 py-1.5 text-sm font-medium ${isActive ? "bg-panel text-white" : "text-muted hover:text-white"}`;

export default function App() {
  return (
    <div className="mx-auto max-w-6xl px-4 pb-16">
      <header className="flex items-center gap-6 py-5">
        <NavLink to="/" className="flex items-center gap-2 text-lg font-semibold text-white">
          <span className="grid h-7 w-7 place-items-center rounded-md bg-accent">
            <svg width="18" height="18" viewBox="0 0 32 32"><circle cx="10" cy="16" r="4.5" fill="#0b0d12" /><circle cx="22" cy="16" r="4.5" fill="#0b0d12" /></svg>
          </span>
          Tapedeck
        </NavLink>
        <nav className="flex gap-1">
          <NavLink to="/" end className={link}>Tapes</NavLink>
          <NavLink to="/servers" className={link}>Servers</NavLink>
          <NavLink to="/runs" className={link}>Runs</NavLink>
        </nav>
      </header>
      <Routes>
        <Route path="/" element={<Tapes />} />
        <Route path="/tapes/:name" element={<TapeDetail />} />
        <Route path="/servers" element={<Servers />} />
        <Route path="/runs" element={<Runs />} />
        <Route path="/runs/:id" element={<JobView />} />
        <Route path="*" element={<p className="text-muted">Nothing here. <NavLink className="text-accent" to="/">Back to tapes</NavLink></p>} />
      </Routes>
    </div>
  );
}
