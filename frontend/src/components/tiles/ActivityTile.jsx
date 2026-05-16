import React from "react";
import { GripVertical } from "lucide-react";
import { useTheme } from "../../hooks/useTheme.js";

export default function ActivityTile({ dragHandleProps, bars = [] }) {
  const { isDark } = useTheme();
  const max = Math.max(1, ...bars.map((b) => b.value));
  const activeIndex = bars.length - 1;

  return (
    <div className="tile">
      <div className="flex items-center justify-between mb-3">
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

      <div key={String(isDark)} className="flex items-end gap-1 h-28">
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
          return (
            <div
              key={i}
              title={`${b.label}: ${b.value}`}
              className={`flex-1 rounded-sm border transition-all duration-300 ${
                active
                  ? "bg-indigo-500 dark:bg-indigo-400 border-indigo-500 dark:border-indigo-400"
                  : "bg-light-hover dark:bg-dark-hover border-light-border dark:border-dark-border"
              }`}
              style={{ height: `${h}%` }}
            />
          );
        })}
      </div>

      <div className="flex justify-between mt-2 text-[10px] text-zinc-400 dark:text-slate-500">
        <span>{bars[0]?.label || ""}</span>
        <span>сегодня</span>
      </div>
    </div>
  );
}
