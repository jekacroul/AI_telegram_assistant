import React, { useState } from "react";
import { GripVertical } from "lucide-react";
import { useTheme } from "../../hooks/useTheme.js";

export default function ActivityTile({ dragHandleProps, bars = [] }) {
  const { isDark } = useTheme();
  const [hovered, setHovered] = useState(null);
  const max = Math.max(1, ...bars.map((b) => b.value));
  const activeIndex = bars.length - 1;

  const hoveredBar = hovered != null ? bars[hovered] : null;
  const hoveredHeight = hoveredBar
    ? Math.max(6, (hoveredBar.value / max) * 100)
    : 0;
  const tooltipLeft =
    hovered != null && bars.length
      ? Math.min(90, Math.max(10, ((hovered + 0.5) / bars.length) * 100))
      : 50;

  return (
    <div className="tile flex flex-col">
      <div className="flex items-center justify-between mb-3 flex-shrink-0">
        <span className="tile-label mb-0">Активность · 24 дня</span>
        <span
          {...dragHandleProps}
          className="cursor-grab active:cursor-grabbing text-zinc-300
                     dark:text-slate-600 hover:text-zinc-500
                     dark:hover:text-slate-400 touch-none"
        >
          <GripVertical size={16} />
        </span>
      </div>

      <div className="relative flex-1 min-h-0">
        {hoveredBar && (
          <div
            className="absolute z-10 pointer-events-none"
            style={{
              left: `${tooltipLeft}%`,
              bottom: `${hoveredHeight}%`,
              transform: "translate(-50%, -8px)",
            }}
          >
            <div
              className="px-2.5 py-1.5 rounded-lg shadow-lg whitespace-nowrap
                         bg-light-card dark:bg-dark-card
                         border border-light-border dark:border-dark-border"
            >
              <div className="text-[10px] text-zinc-400 dark:text-slate-500">
                {hoveredBar.label}
              </div>
              <div className="text-xs font-semibold text-zinc-800 dark:text-slate-200">
                {hoveredBar.value} сообщений
              </div>
            </div>
          </div>
        )}

        <div key={String(isDark)} className="flex items-end gap-1 h-full">
          {bars.length === 0 &&
            Array.from({ length: 24 }).map((_, i) => (
              <div
                key={i}
                className="flex-1 rounded-sm bg-light-hover dark:bg-dark-hover
                           border border-light-border dark:border-dark-border"
                style={{ height: "8%" }}
              />
            ))}
          {bars.map((b, i) => {
            const h = Math.max(6, (b.value / max) * 100);
            const active = i === activeIndex;
            const isHovered = i === hovered;
            return (
              <div
                key={i}
                onMouseEnter={() => setHovered(i)}
                onMouseLeave={() => setHovered((cur) => (cur === i ? null : cur))}
                className={`flex-1 rounded-sm border transition-all duration-150 cursor-pointer ${
                  active
                    ? "bg-indigo-500 dark:bg-indigo-400 border-indigo-500 dark:border-indigo-400"
                    : "bg-light-hover dark:bg-dark-hover border-light-border dark:border-dark-border"
                } ${
                  isHovered
                    ? "ring-1 ring-indigo-400 dark:ring-indigo-300 brightness-110"
                    : ""
                }`}
                style={{ height: `${h}%` }}
              />
            );
          })}
        </div>
      </div>

      <div className="flex justify-between mt-2 flex-shrink-0 text-[10px] text-zinc-400 dark:text-slate-500">
        <span>{bars[0]?.label || ""}</span>
        <span>сегодня</span>
      </div>
    </div>
  );
}
