import React from "react";

const ACCENT = {
  indigo:
    "text-indigo-500 dark:text-indigo-400 bg-indigo-100 dark:bg-indigo-950/40",
  sky: "text-sky-500 dark:text-sky-400 bg-sky-100 dark:bg-sky-950/40",
  emerald:
    "text-emerald-500 dark:text-emerald-400 bg-emerald-100 dark:bg-emerald-950/40",
  violet:
    "text-violet-500 dark:text-violet-400 bg-violet-100 dark:bg-violet-950/40",
  amber: "text-amber-500 dark:text-amber-400 bg-amber-100 dark:bg-amber-950/40",
  rose: "text-rose-500 dark:text-rose-400 bg-rose-100 dark:bg-rose-950/40",
};

export default function MetricCard({
  label,
  value,
  hint,
  icon: Icon,
  accent = "indigo",
}) {
  return (
    <div className="tile">
      <div className="flex items-center justify-between gap-2 mb-3">
        <span className="tile-label mb-0 truncate">{label}</span>
        {Icon && (
          <span
            className={`w-7 h-7 rounded-lg flex items-center justify-center
              flex-shrink-0 ${ACCENT[accent] || ACCENT.indigo}`}
          >
            <Icon size={15} />
          </span>
        )}
      </div>
      <p className="tile-value tabular-nums text-zinc-900 dark:text-slate-100">
        {value}
      </p>
      {hint && (
        <p
          className="text-xs text-zinc-400 dark:text-slate-500 mt-1 truncate"
          title={hint}
        >
          {hint}
        </p>
      )}
    </div>
  );
}
