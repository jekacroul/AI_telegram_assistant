import React from "react";
import { NavLink } from "react-router-dom";
import {
  MessageSquare,
  Archive,
  GraduationCap,
  SlidersHorizontal,
  BarChart2,
  Settings,
  Zap,
  HardDrive,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import ThemeToggle from "./ThemeToggle.jsx";

export const NAV_ITEMS = [
  { to: "/", label: "Дашборд", icon: MessageSquare },
  { to: "/dialogs", label: "Диалоги", icon: Archive },
  { to: "/training", label: "Обучение", icon: GraduationCap },
  { to: "/style", label: "Стиль", icon: SlidersHorizontal },
  { to: "/stats", label: "Статистика", icon: BarChart2 },
  { to: "/quick-replies", label: "Быстрые ответы", icon: Zap },
  { to: "/replication", label: "Репликация", icon: HardDrive },
  { to: "/settings", label: "Настройки", icon: Settings },
];

export default function Sidebar({ collapsed, onToggleCollapsed, theme, onToggleTheme }) {
  return (
    <aside
      className={`flex flex-col h-full border-r border-line bg-sidebar
                  transition-all duration-200 ${collapsed ? "w-14" : "w-[220px]"}`}
    >
      <div
        className={`flex items-center gap-2.5 h-[52px] border-b border-line
                    ${collapsed ? "justify-center px-0" : "px-4"}`}
      >
        <div className="w-6 h-6 rounded bg-accent flex-shrink-0" />
        {!collapsed && (
          <span className="text-sm font-semibold text-fg truncate">
            Telegram AI
          </span>
        )}
      </div>

      <nav className="flex-1 overflow-y-auto p-2 space-y-0.5">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            title={collapsed ? label : undefined}
            className={({ isActive }) =>
              `nav-item ${isActive ? "nav-item-active" : ""} ${
                collapsed ? "justify-center px-0" : ""
              }`
            }
          >
            <Icon size={16} className="flex-shrink-0" />
            {!collapsed && <span className="truncate">{label}</span>}
          </NavLink>
        ))}
      </nav>

      <div className="p-2 border-t border-line space-y-0.5">
        <ThemeToggle theme={theme} onToggle={onToggleTheme} collapsed={collapsed} />
        <button
          className={`nav-item w-full ${collapsed ? "justify-center px-0" : ""}`}
          onClick={onToggleCollapsed}
          title={collapsed ? "Развернуть" : "Свернуть"}
        >
          {collapsed ? (
            <PanelLeftOpen size={16} />
          ) : (
            <PanelLeftClose size={16} />
          )}
          {!collapsed && <span>Свернуть</span>}
        </button>
      </div>
    </aside>
  );
}
