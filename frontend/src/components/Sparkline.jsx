import React, { useMemo } from "react";
import { useTheme } from "../hooks/useTheme.js";
import { pickColor } from "../lib/colors.js";

export default function Sparkline({
  data = [],
  color = "indigo",
  height = 40,
  fill = true,
}) {
  const { isDark } = useTheme();
  const stroke = pickColor(color, isDark);

  const { path, area, w, h } = useMemo(() => {
    const h = height;
    const w = 260;
    if (!data.length) return { path: "", area: "", w, h };
    const max = Math.max(...data, 1);
    const min = Math.min(...data, 0);
    const range = max - min || 1;
    const step = data.length > 1 ? w / (data.length - 1) : w;
    const pts = data.map((v, i) => [
      i * step,
      h - ((v - min) / range) * (h - 6) - 3,
    ]);
    const line = pts
      .map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`)
      .join(" ");
    return { path: line, area: `${line} L ${w} ${h} L 0 ${h} Z`, w, h };
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
    <svg
      key={String(isDark)}
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      className="w-full"
      style={{ height }}
    >
      {fill && <path d={area} fill={stroke} opacity="0.12" />}
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
          animation: "spark-draw .6s ease forwards",
        }}
      />
    </svg>
  );
}
