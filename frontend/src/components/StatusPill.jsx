import React from "react";

export default function StatusPill({ ok, label, pending }) {
  const dot = pending
    ? "bg-amber-400 dark:bg-amber-300 animate-pulse-dot"
    : ok
    ? "bg-emerald-500 dark:bg-emerald-400"
    : "bg-rose-500 dark:bg-rose-400";
  return (
    <span className="status-pill">
      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${dot}`} />
      {label}
    </span>
  );
}
