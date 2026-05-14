import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, NavLink, Navigate } from "react-router-dom";
import "./index.css";
import Dashboard from "./pages/Dashboard.jsx";
import Dialogs from "./pages/Dialogs.jsx";
import Training from "./pages/Training.jsx";
import Replication from "./pages/Replication.jsx";
import StyleProfile from "./pages/StyleProfile.jsx";
import Settings from "./pages/Settings.jsx";
import Stats from "./pages/Stats.jsx";
import QuickReplies from "./pages/QuickReplies.jsx";

function Layout() {
  const linkBase = "px-4 py-2 rounded-md text-sm transition-colors";
  const linkActive = "bg-accent text-white";
  const linkInactive = "text-muted hover:bg-panel hover:text-white";

  return (
    <div className="min-h-screen bg-bg text-white">
      <header className="border-b border-white/5 bg-panel/60 backdrop-blur sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-6 py-3 flex items-center gap-6">
          <div className="font-semibold text-lg">Telegram Local AI</div>
          <nav className="flex items-center gap-1">
            {[
              ["/", "Дашборд"],
              ["/dialogs", "Диалоги"],
              ["/training", "Обучение"],
              ["/replication", "Репликация"],
              ["/style", "Стиль"],
              ["/stats", "Статистика"],
              ["/settings", "Настройки"],
              ["/quick-replies", "Быстрые ответы"],
            ].map(([to, label]) => (
              <NavLink
                key={to}
                to={to}
                end={to === "/"}
                className={({ isActive }) =>
                  `${linkBase} ${isActive ? linkActive : linkInactive}`
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-6 py-6">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/dialogs" element={<Dialogs />} />
          <Route path="/training" element={<Training />} />
          <Route path="/replication" element={<Replication />} />
          <Route path="/style" element={<StyleProfile />} />
          <Route path="/stats" element={<Stats />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/quick-replies" element={<QuickReplies />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <Layout />
    </BrowserRouter>
  </React.StrictMode>
);
