import { NavLink, Route, Routes, Navigate } from "react-router-dom";
import { useEffect, useState } from "react";
import Dashboard from "./pages/Dashboard.jsx";
import Training from "./pages/Training.jsx";
import StyleProfile from "./pages/StyleProfile.jsx";
import Settings from "./pages/Settings.jsx";
import { api } from "./api.js";

function StatusBadge({ status }) {
  const running = status?.running;
  return (
    <div className="flex items-center gap-2 text-sm">
      <span
        className={`inline-block h-2.5 w-2.5 rounded-full ${
          running ? "bg-emerald-400" : "bg-rose-500"
        }`}
      />
      <span className="text-slate-300">
        Ollama: {running ? status.model : "offline"}
      </span>
      {status?.active_adapter && (
        <span className="ml-2 rounded bg-brand-700/40 px-2 py-0.5 text-xs">
          adapter: {status.active_adapter.split("/").pop()}
        </span>
      )}
    </div>
  );
}

function navClass({ isActive }) {
  return `block rounded px-3 py-2 text-sm transition ${
    isActive
      ? "bg-brand-600 text-white"
      : "text-slate-300 hover:bg-slate-800 hover:text-white"
  }`;
}

export default function App() {
  const [llm, setLlm] = useState(null);

  useEffect(() => {
    let alive = true;
    async function tick() {
      try {
        const s = await api.llmStatus();
        if (alive) setLlm(s);
      } catch (_) {
        if (alive) setLlm({ running: false });
      }
    }
    tick();
    const id = setInterval(tick, 8000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return (
    <div className="flex h-full">
      <aside className="flex w-56 flex-col gap-1 border-r border-slate-800 bg-slate-900/60 p-4">
        <div className="mb-4">
          <h1 className="text-lg font-semibold text-white">Telegram Local AI</h1>
          <p className="text-xs text-slate-400">your style, locally</p>
        </div>
        <NavLink to="/dashboard" className={navClass}>Dashboard</NavLink>
        <NavLink to="/training" className={navClass}>Training</NavLink>
        <NavLink to="/style" className={navClass}>Style profile</NavLink>
        <NavLink to="/settings" className={navClass}>Settings</NavLink>
        <div className="mt-auto pt-4">
          <StatusBadge status={llm} />
        </div>
      </aside>
      <main className="flex-1 overflow-auto p-6">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/training" element={<Training />} />
          <Route path="/style" element={<StyleProfile />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}
