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

export const NAV_ITEMS = [
  { to: "/", label: "Дашборд", icon: LayoutDashboard },
  { to: "/dialogs", label: "Диалоги", icon: Archive },
  { to: "/training", label: "Обучение", icon: GraduationCap },
  { to: "/style", label: "Стиль", icon: SlidersHorizontal },
  { to: "/stats", label: "Статистика", icon: BarChart2 },
  { to: "/quick-replies", label: "Быстрые ответы", icon: Zap },
  { to: "/replication", label: "Репликация", icon: HardDrive },
  { to: "/settings", label: "Настройки", icon: Settings },
];

export default function Sidebar() {
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
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            title={label}
            className={({ isActive }) =>
              `nav-item ${isActive ? "nav-item-active" : ""}`
            }
          >
            <Icon size={17} />
          </NavLink>
        ))}
      </nav>

      <div className="flex-1" />
      <ThemeToggle />
    </aside>
  );
}
