import React from "react";

export default function Tabs({ tabs, value, onChange }) {
  return (
    <div
      className="flex flex-wrap gap-1 p-1 rounded-xl
                 bg-light-card2 dark:bg-dark-card2
                 border border-light-border dark:border-dark-border"
    >
      {tabs.map((tb) => (
        <button
          key={tb.id}
          onClick={() => onChange(tb.id)}
          className={`flex items-center gap-2 px-3 h-8 rounded-lg text-sm
            font-medium transition-colors ${
              value === tb.id
                ? "bg-light-card dark:bg-dark-card text-indigo-600 dark:text-indigo-400 shadow-sm"
                : "text-zinc-500 dark:text-slate-400 hover:text-zinc-700 dark:hover:text-slate-200"
            }`}
        >
          {tb.icon && <tb.icon size={14} />}
          {tb.label}
        </button>
      ))}
    </div>
  );
}
