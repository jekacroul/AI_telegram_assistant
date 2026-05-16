import React, { useState } from "react";
import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import Sidebar from "./Sidebar.jsx";
import Header from "./Header.jsx";
import { useTheme } from "../hooks/useTheme.js";
import Dashboard from "../pages/Dashboard.jsx";
import Dialogs from "../pages/Dialogs.jsx";
import Training from "../pages/Training.jsx";
import StyleProfile from "../pages/StyleProfile.jsx";
import Settings from "../pages/Settings.jsx";
import Stats from "../pages/Stats.jsx";
import QuickReplies from "../pages/QuickReplies.jsx";
import Replication from "../pages/Replication.jsx";

const PAGE_META = {
  "/": { title: "Дашборд", subtitle: "Входящие сообщения и ответы" },
  "/dialogs": { title: "Диалоги", subtitle: "Резервные копии переписок" },
  "/training": { title: "Обучение", subtitle: "Датасеты и fine-tuning модели" },
  "/style": { title: "Стиль", subtitle: "Профиль общения и персоны" },
  "/stats": { title: "Статистика", subtitle: "Активность и качество ответов" },
  "/quick-replies": { title: "Быстрые ответы", subtitle: "Шаблоны частых ответов" },
  "/replication": { title: "Репликация", subtitle: "Резервное копирование базы" },
  "/settings": { title: "Настройки", subtitle: "Конфигурация ассистента" },
};

export default function Layout() {
  const { theme, toggle } = useTheme();
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem("sidebar-collapsed") === "1";
    } catch {
      return false;
    }
  });
  const location = useLocation();
  const meta = PAGE_META[location.pathname] || {
    title: "Telegram AI",
    subtitle: "",
  };

  const toggleCollapsed = () => {
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem("sidebar-collapsed", next ? "1" : "0");
      } catch {
        // ignore
      }
      return next;
    });
  };

  return (
    <div className="flex h-screen bg-bg text-fg overflow-hidden">
      <Sidebar
        collapsed={collapsed}
        onToggleCollapsed={toggleCollapsed}
        theme={theme}
        onToggleTheme={toggle}
      />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header title={meta.title} subtitle={meta.subtitle} />
        <main className="flex-1 overflow-auto p-6 lg:p-8">
          <div className="max-w-6xl mx-auto animate-fadeIn">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/dialogs" element={<Dialogs />} />
              <Route path="/training" element={<Training />} />
              <Route path="/style" element={<StyleProfile />} />
              <Route path="/stats" element={<Stats />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/quick-replies" element={<QuickReplies />} />
              <Route path="/replication" element={<Replication />} />
              <Route path="*" element={<Navigate to="/" />} />
            </Routes>
          </div>
        </main>
      </div>
    </div>
  );
}
