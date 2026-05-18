import React, { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Archive,
  GraduationCap,
  SlidersHorizontal,
  BarChart2,
  Zap,
  HardDrive,
  Settings,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import ThemeToggle from "./ThemeToggle.jsx";
import LanguageToggle from "./LanguageToggle.jsx";
import { useLang } from "../hooks/useLang.js";

export const NAV_ITEMS = [
  { to: "/", labelKey: "nav.dashboard", icon: LayoutDashboard },
  { to: "/dialogs", labelKey: "nav.dialogs", icon: Archive },
  { to: "/training", labelKey: "nav.training", icon: GraduationCap },
  { to: "/style", labelKey: "nav.style", icon: SlidersHorizontal },
  { to: "/stats", labelKey: "nav.stats", icon: BarChart2 },
  { to: "/quick-replies", labelKey: "nav.quickReplies", icon: Zap },
  { to: "/replication", labelKey: "nav.replication", icon: HardDrive },
  { to: "/settings", labelKey: "nav.settings", icon: Settings },
];

function getInitialCollapsed() {
  try {
    return localStorage.getItem("sidebarCollapsed") !== "false";
  } catch {
    return true;
  }
}

export default function Sidebar() {
  const { t } = useLang();
  const [collapsed, setCollapsed] = useState(getInitialCollapsed);

  useEffect(() => {
    try {
      localStorage.setItem("sidebarCollapsed", String(collapsed));
    } catch {
      // ignore
    }
  }, [collapsed]);

  return (
    <aside
      className={`${collapsed ? "w-14 items-center" : "w-52 items-stretch"}
                 flex-shrink-0 flex flex-col gap-1 py-3 px-2
                 bg-light-card dark:bg-dark-card
                 border-r border-light-border dark:border-dark-border
                 transition-[width] duration-200`}
    >
      <div
        className={`flex items-center gap-2 mb-2 ${
          collapsed ? "justify-center" : "px-1"
        }`}
      >
        <div className="w-9 h-9 rounded-xl flex items-center justify-center
                        bg-indigo-600 dark:bg-indigo-500 text-white font-bold text-sm
                        flex-shrink-0">
          AI
        </div>
        {!collapsed && (
          <span className="font-semibold text-sm whitespace-nowrap
                           text-zinc-700 dark:text-slate-200">
            {t("appTitle")}
          </span>
        )}
      </div>

      <nav className="flex flex-col gap-1">
        {NAV_ITEMS.map(({ to, labelKey, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            title={collapsed ? t(labelKey) : undefined}
            className={({ isActive }) =>
              `${collapsed ? "nav-item" : "nav-item-row"} ${
                isActive ? "nav-item-active" : ""
              }`
            }
          >
            <Icon size={17} className="flex-shrink-0" />
            {!collapsed && (
              <span className="whitespace-nowrap">{t(labelKey)}</span>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="flex-1" />

      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        title={collapsed ? t("nav.expand") : t("nav.collapse")}
        className={collapsed ? "nav-item" : "nav-item-row"}
      >
        {collapsed ? (
          <PanelLeftOpen size={17} className="flex-shrink-0" />
        ) : (
          <PanelLeftClose size={17} className="flex-shrink-0" />
        )}
        {!collapsed && (
          <span className="whitespace-nowrap">{t("nav.collapse")}</span>
        )}
      </button>

      <div
        className={`flex gap-1 ${
          collapsed ? "flex-col items-center" : "items-center justify-center"
        }`}
      >
        <LanguageToggle />
        <ThemeToggle />
      </div>
    </aside>
  );
}
