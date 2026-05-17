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
import { useLang } from "../hooks/useLang.js";

const PAGE_META = {
  "/": { titleKey: "nav.dashboard", subtitleKey: "subtitle.dashboard" },
  "/dialogs": { titleKey: "nav.dialogs", subtitleKey: "subtitle.dialogs" },
  "/training": { titleKey: "nav.training", subtitleKey: "subtitle.training" },
  "/style": { titleKey: "nav.style", subtitleKey: "subtitle.style" },
  "/stats": { titleKey: "nav.stats", subtitleKey: "subtitle.stats" },
  "/quick-replies": {
    titleKey: "nav.quickReplies",
    subtitleKey: "subtitle.quickReplies",
  },
  "/replication": {
    titleKey: "nav.replication",
    subtitleKey: "subtitle.replication",
  },
  "/settings": { titleKey: "nav.settings", subtitleKey: "subtitle.settings" },
};

export default function Layout() {
  const location = useLocation();
  const { t } = useLang();
  const meta = PAGE_META[location.pathname];
  const title = meta ? t(meta.titleKey) : t("appTitle");
  const subtitle = meta ? t(meta.subtitleKey) : "";

  return (
    <div className="flex h-screen overflow-hidden bg-light-bg dark:bg-dark-bg">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header title={title} subtitle={subtitle} />
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
