import React from "react";

export default function StatusPill({ ok, label, pending }) {
  const color = pending
    ? "bg-amber"
    : ok
    ? "bg-good"
    : "bg-bad";
  return (
    <div
      className="flex items-center gap-1.5 px-2.5 py-1 rounded-full
                 border border-line text-xs text-muted"
    >
      <span
        className={`w-1.5 h-1.5 rounded-full ${color} ${
          ok || pending ? "animate-pulse" : ""
        }`}
      />
      {label}
    </div>
  );
}
