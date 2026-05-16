import React from "react";
import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import Sidebar from "./Sidebar.jsx";
import Header from "./Header.jsx";
import Dashboard from "../pages/Dashboard.jsx";
import Dialogs from "../pages/Dialogs.jsx";
import Training from "../pages/Training.jsx";
import StyleProfile from "../pages/StyleProfile.jsx";
import Settings from "../pages/Settings.jsx";
import Stats from "../pages/Stats.jsx";
import QuickReplies from "../pages/QuickReplies.jsx";
import Replication from "../pages/Replication.jsx";

const PAGE_META = {
  "/": { title: "Дашборд", subtitle: "Обзор ассистента" },
  "/dialogs": { title: "Диалоги", subtitle: "Резервные копии переписок" },
  "/training": { title: "Обучение", subtitle: "Датасеты и fine-tuning" },
  "/style": { title: "Стиль", subtitle: "Профиль общения и персоны" },
  "/stats": { title: "Статистика", subtitle: "Активность и качество" },
  "/quick-replies": { title: "Быстрые ответы", subtitle: "Шаблоны ответов" },
  "/replication": { title: "Репликация", subtitle: "Резервное копирование базы" },
  "/settings": { title: "Настройки", subtitle: "Конфигурация ассистента" },
};

export default function Layout() {
  const location = useLocation();
  const meta = PAGE_META[location.pathname] || {
    title: "Telegram AI",
    subtitle: "",
  };

  return (
    <div className="flex h-screen overflow-hidden bg-light-bg dark:bg-dark-bg">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header title={meta.title} subtitle={meta.subtitle} />
        <main className="flex-1 overflow-auto p-6">
          <div className="max-w-[1400px] mx-auto animate-fade-in">
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
