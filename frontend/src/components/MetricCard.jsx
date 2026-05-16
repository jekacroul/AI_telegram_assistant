import React from "react";

export default function MetricCard({ label, value, hint, icon: Icon }) {
  return (
    <div className="card">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted">{label}</p>
        {Icon && <Icon size={14} className="text-muted" />}
      </div>
      <p className="text-2xl font-semibold tabular-nums mt-1 truncate text-fg">
        {value}
      </p>
      {hint && (
        <p className="text-xs text-muted mt-1 truncate" title={hint}>
          {hint}
        </p>
      )}
    </div>
  );
}
