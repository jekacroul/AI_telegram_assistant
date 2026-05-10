import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, NavLink, Navigate } from "react-router-dom";
import "./index.css";
import Dashboard from "./pages/Dashboard.jsx";
import Training from "./pages/Training.jsx";
import StyleProfile from "./pages/StyleProfile.jsx";
import Settings from "./pages/Settings.jsx";

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
              ["/training", "Обучение"],
              ["/style", "Стиль"],
              ["/settings", "Настройки"],
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
          <Route path="/training" element={<Training />} />
          <Route path="/style" element={<StyleProfile />} />
          <Route path="/settings" element={<Settings />} />
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
