import React from "react";
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

export default function Sidebar() {
  const { t } = useLang();
  return (
    <aside
      className="w-14 flex-shrink-0 flex flex-col items-center gap-1 py-3
                 bg-light-card dark:bg-dark-card
                 border-r border-light-border dark:border-dark-border"
    >
      <div className="w-9 h-9 rounded-xl mb-2 flex items-center justify-center
                      bg-indigo-600 dark:bg-indigo-500 text-white font-bold text-sm">
        AI
      </div>

      <nav className="flex flex-col items-center gap-1">
        {NAV_ITEMS.map(({ to, labelKey, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            title={t(labelKey)}
            className={({ isActive }) =>
              `nav-item ${isActive ? "nav-item-active" : ""}`
            }
          >
            <Icon size={17} />
          </NavLink>
        ))}
      </nav>

      <div className="flex-1" />
      <LanguageToggle />
      <ThemeToggle />
    </aside>
  );
}
