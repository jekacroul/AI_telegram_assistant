import React from "react";

export default function StatusDot({ ok, label }) {
  return (
    <div className="flex items-center gap-2">
      <span
        className={`w-2.5 h-2.5 rounded-full ${
          ok ? "bg-good" : "bg-bad"
        }`}
      />
      <span className="text-sm text-muted">
        {label}: <span className={ok ? "text-good" : "text-bad"}>{ok ? "✓" : "✗"}</span>
      </span>
    </div>
  );
}
