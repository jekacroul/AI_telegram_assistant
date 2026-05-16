import React from "react";
import { GripVertical, Maximize2, Minimize2 } from "lucide-react";
import Sparkline from "../Sparkline.jsx";
import { useCountUp } from "../../hooks/useCountUp.js";

export default function MetricTile({
  dragHandleProps,
  label,
  value,
  format = (v) => Math.round(v).toLocaleString("ru-RU"),
  accent = "indigo",
  series = null,
  expanded = false,
  onToggleExpand,
  children,
}) {
  const animated = useCountUp(Number(value) || 0);

  return (
    <div className="tile">
      <div className="flex items-center justify-between mb-3">
        <span className="tile-label mb-0">{label}</span>
        <div className="flex items-center gap-1.5">
          <button
            className="expand-btn"
            onClick={onToggleExpand}
            title={expanded ? "Свернуть" : "Развернуть"}
          >
            {expanded ? <Minimize2 size={12} /> : <Maximize2 size={12} />}
          </button>
          <span
            {...dragHandleProps}
            className="cursor-grab active:cursor-grabbing text-zinc-300
                       dark:text-slate-600 hover:text-zinc-500
                       dark:hover:text-slate-400 touch-none"
          >
            <GripVertical size={16} />
          </span>
        </div>
      </div>

      <div className="tile-value text-zinc-900 dark:text-slate-100 animate-count-up">
        {format(animated)}
      </div>

      {series && series.length > 0 && (
        <div className="mt-2">
          <Sparkline data={series} color={accent} height={expanded ? 100 : 40} />
        </div>
      )}

      {expanded && children && (
        <div className="expanded-section">{children}</div>
      )}
    </div>
  );
}
