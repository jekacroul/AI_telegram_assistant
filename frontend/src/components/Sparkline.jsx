import React, { useId, useMemo } from "react";
import { useTheme } from "../hooks/useTheme.js";
import { pickColor } from "../lib/colors.js";

// Catmull-Rom spline rendered as cubic beziers — smooth curve through points.
function smoothPath(pts) {
  if (pts.length < 2) {
    return pts.length ? `M${pts[0][0]} ${pts[0][1]}` : "";
  }
  const d = [`M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`];
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] || p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6;
    const c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6;
    const c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d.push(
      `C${c1x.toFixed(1)} ${c1y.toFixed(1)} ` +
        `${c2x.toFixed(1)} ${c2y.toFixed(1)} ` +
        `${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`,
    );
  }
  return d.join(" ");
}

export default function Sparkline({
  data = [],
  color = "indigo",
  height = 40,
  fill = true,
}) {
  const { isDark } = useTheme();
  const stroke = pickColor(color, isDark);
  const uid = useId().replace(/:/g, "");

  const { path, area, last, w, h } = useMemo(() => {
    const h = height;
    const w = 260;
    if (!data.length) return { path: "", area: "", last: null, w, h };
    const pad = 4;
    const max = Math.max(...data, 1);
    const min = Math.min(...data, 0);
    const range = max - min || 1;
    const step = data.length > 1 ? w / (data.length - 1) : w;
    const pts = data.map((v, i) => [
      i * step,
      h - ((v - min) / range) * (h - pad * 2) - pad,
    ]);
    const line = smoothPath(pts);
    return {
      path: line,
      area: `${line} L ${w} ${h} L 0 ${h} Z`,
      last: pts[pts.length - 1],
      w,
      h,
    };
  }, [data, height]);

  if (!data.length) {
    return (
      <div
        className="w-full rounded bg-light-card2 dark:bg-dark-card2"
        style={{ height }}
      />
    );
  }

  return (
    <div className="relative w-full" style={{ height }}>
      <svg
        key={String(isDark)}
        viewBox={`0 0 ${w} ${h}`}
        preserveAspectRatio="none"
        className="w-full block"
        style={{ height }}
      >
        <defs>
          <linearGradient id={`sparkFill${uid}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.34" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>
        {fill && <path d={area} fill={`url(#sparkFill${uid})`} />}
        <path
          d={path}
          fill="none"
          stroke={stroke}
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{
            strokeDasharray: 1200,
            strokeDashoffset: 1200,
            animation: "spark-draw .7s ease forwards",
          }}
        />
      </svg>
      {last && (
        <span
          className="spark-dot"
          style={{
            left: "100%",
            top: `${(last[1] / h) * 100}%`,
            color: stroke,
          }}
        />
      )}
    </div>
  );
}
