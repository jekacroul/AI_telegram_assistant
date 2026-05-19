import React from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  GripVertical,
  Maximize2,
  Minimize2,
  Minus,
} from "lucide-react";
import Sparkline from "../Sparkline.jsx";
import { useCountUp } from "../../hooks/useCountUp.js";
import { useLang } from "../../hooks/useLang.js";

function DeltaBadge({ delta }) {
  const { t } = useLang();
  const up = delta > 0;
  const down = delta < 0;
  const Icon = up ? ArrowUpRight : down ? ArrowDownRight : Minus;
  const tone = up
    ? "text-emerald-500 dark:text-emerald-400"
    : down
      ? "text-rose-500 dark:text-rose-400"
      : "text-zinc-400 dark:text-slate-500";

  return (
    <div className="flex items-center gap-1 text-xs font-medium mb-1">
      <Icon size={13} className={tone} />
      <span className={tone}>
        {delta > 0 ? `+${delta}` : delta}
      </span>
      <span className="text-zinc-400 dark:text-slate-500">
        {t("dashboard.vsYesterday")}
      </span>
    </div>
  );
}

export default function MetricTile({
  dragHandleProps,
  label,
  value,
  format = (v) => Math.round(v).toLocaleString(),
  accent = "indigo",
  series = null,
  delta = null,
  expanded = false,
  onToggleExpand,
  children,
}) {
  const { t } = useLang();
  const animated = useCountUp(Number(value) || 0);

  return (
    <div className="tile">
      <div className="flex items-center justify-between mb-3">
        <span className="tile-label mb-0">{label}</span>
        <div className="flex items-center gap-1.5">
          <button
            className="expand-btn"
            onClick={onToggleExpand}
            title={expanded ? t("tiles.collapse") : t("tiles.expand")}
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

      {delta != null && <DeltaBadge delta={delta} />}

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
