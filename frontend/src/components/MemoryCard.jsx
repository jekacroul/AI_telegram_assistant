import React, { useEffect, useState } from "react";

const COLORS = {
  amber: {
    text: "text-amber-400 dark:text-amber-300",
    fill: "bg-amber-400 dark:bg-amber-300",
  },
  emerald: {
    text: "text-emerald-400 dark:text-emerald-300",
    fill: "bg-emerald-400 dark:bg-emerald-300",
  },
};

export default function MemoryCard({ label, value, sub, percent = 0, color = "emerald" }) {
  const c = COLORS[color] || COLORS.emerald;
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => setWidth(Math.min(100, Math.max(0, percent))), 60);
    return () => clearTimeout(t);
  }, [percent]);

  return (
    <div className="mem-card">
      <div className="tile-label">{label}</div>
      <div className={`text-xl font-bold leading-none mb-1 ${c.text}`}>
        {value}
      </div>
      <div className="stat-label text-xs mb-2">{sub}</div>
      <div className="progress-bar">
        <div
          className={`progress-fill ${c.fill}`}
          style={{ width: `${width}%` }}
        />
      </div>
    </div>
  );
}
