import React, { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../lib/api.js";
import MetricCard from "../components/MetricCard.jsx";
import EmptyState from "../components/EmptyState.jsx";
import { BarChart2 } from "lucide-react";
import { useTheme } from "../hooks/useTheme.js";
import { chartColors } from "../lib/colors.js";

function useChartColors() {
  const { isDark } = useTheme();
  const c = chartColors(isDark);
  return {
    grid: c.grid,
    axis: c.axisText,
    line1: c.primary,
    line2: c.secondary,
    bar: c.primary,
    tooltipBg: c.tooltipBg,
    tooltipBorder: c.tooltipBorder,
    cursor: isDark ? "rgba(129,140,248,0.10)" : "rgba(79,70,229,0.08)",
  };
}

function formatPercent(value) {
  if (value == null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  if (seconds < 60) return `${Math.round(seconds)} с`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `${m} мин ${s} с`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return `${h} ч ${mm} мин`;
}

function shortDay(day) {
  if (!day) return "";
  const parts = day.split("-");
  return parts.length === 3 ? `${parts[2]}.${parts[1]}` : day;
}

function formatUsername(username) {
  if (!username) return "—";
  return username.startsWith("@") ? username : `@${username}`;
}

const TH =
  "px-4 py-3 text-2xs font-medium uppercase tracking-wider text-muted";

export default function Stats() {
  const colors = useChartColors();
  const [overview, setOverview] = useState(null);
  const [activity, setActivity] = useState([]);
  const [topChats, setTopChats] = useState([]);
  const [modelQuality, setModelQuality] = useState([]);
  const [responseTime, setResponseTime] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [ov, act, top, mq, rt] = await Promise.all([
          api.statsOverview(),
          api.statsActivity(),
          api.statsTopChats(),
          api.statsModelQuality(),
          api.statsResponseTime(),
        ]);
        if (cancelled) return;
        setOverview(ov);
        setActivity(act);
        setTopChats(top);
        setModelQuality(mq);
        setResponseTime(rt);
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
    }
    load();
    const id = setInterval(load, 30000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const tooltipStyle = {
    background: colors.tooltipBg,
    border: `1px solid ${colors.tooltipBorder}`,
    borderRadius: "8px",
    fontSize: "12px",
  };

  function TopChatTooltip({ active, payload }) {
    if (!active || !payload?.length) return null;
    const item = payload[0].payload;
    return (
      <div style={tooltipStyle} className="px-3 py-2 text-xs">
        <div className="font-medium text-fg">{item.name}</div>
        <div className="text-muted">Ник: {formatUsername(item.username)}</div>
        <div className="text-muted">Сообщений: {item.count}</div>
      </div>
    );
  }

  const activityChartData = activity.map((row) => ({
    ...row,
    label: shortDay(row.day),
  }));

  const topChatsData = topChats.map((c) => ({
    name: c.chat_name || `chat ${c.chat_id}`,
    username: c.chat_username || "",
    count: c.count,
  }));

  const topChatsAxisWidth = Math.min(
    220,
    Math.max(140, ...topChatsData.map((c) => c.name.length * 8))
  );

  return (
    <div className="space-y-6">
      {error && (
        <div className="card border-bad/40 text-sm text-bad">{error}</div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          label="Получено"
          value={overview?.received ?? "—"}
          hint="входящих сообщений"
        />
        <MetricCard
          label="Отправлено"
          value={overview?.sent ?? "—"}
          hint={`отвечено: ${overview?.replied ?? 0}`}
        />
        <MetricCard
          label="Одобрено вручную"
          value={overview?.approved_manual ?? "—"}
          hint={`approval rate: ${formatPercent(overview?.approval_rate)}`}
        />
        <MetricCard
          label="Отклонено фильтром"
          value={overview?.rejected ?? "—"}
          hint="плохой фидбек"
        />
      </div>

      <div className="card">
        <p className="section-label">Активность за 30 дней</p>
        <div className="h-64">
          <ResponsiveContainer>
            <LineChart data={activityChartData}>
              <CartesianGrid stroke={colors.grid} strokeDasharray="3 3" />
              <XAxis
                dataKey="label"
                stroke={colors.axis}
                fontSize={11}
                tickLine={false}
              />
              <YAxis
                stroke={colors.axis}
                fontSize={11}
                tickLine={false}
                allowDecimals={false}
              />
              <Tooltip contentStyle={tooltipStyle} />
              <Line
                type="monotone"
                dataKey="received"
                name="Получено"
                stroke={colors.line1}
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
              <Line
                type="monotone"
                dataKey="sent"
                name="Отправлено"
                stroke={colors.line2}
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="card">
        <p className="section-label">Топ чатов по числу сообщений</p>
        {topChatsData.length === 0 ? (
          <EmptyState
            icon={BarChart2}
            title="Нет данных"
            description="Статистика появится после первых сообщений"
          />
        ) : (
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart
                data={topChatsData}
                layout="vertical"
                margin={{ left: 8, right: 12 }}
              >
                <CartesianGrid stroke={colors.grid} strokeDasharray="3 3" />
                <XAxis
                  type="number"
                  stroke={colors.axis}
                  fontSize={11}
                  tickLine={false}
                  allowDecimals={false}
                />
                <YAxis
                  type="category"
                  dataKey="name"
                  stroke={colors.axis}
                  fontSize={11}
                  tickLine={false}
                  width={topChatsAxisWidth}
                  interval={0}
                />
                <Tooltip
                  content={<TopChatTooltip />}
                  cursor={{ fill: colors.cursor }}
                  shared={false}
                />
                <Bar dataKey="count" fill={colors.bar} radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      <div className="card overflow-hidden p-0">
        <p className="section-label px-4 pt-4">Качество по версиям модели</p>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left">
              <th className={TH}>Версия</th>
              <th className={TH}>Завершено</th>
              <th className={TH}>Одобрено</th>
              <th className={TH}>Отклонено</th>
              <th className={TH}>Approval</th>
              <th className={TH}>Rejection</th>
              <th className={TH}>Статус</th>
            </tr>
          </thead>
          <tbody>
            {modelQuality.length === 0 && (
              <tr>
                <td colSpan="7" className="text-muted px-4 py-4">
                  Данных пока нет.
                </td>
              </tr>
            )}
            {modelQuality.map((m) => (
              <tr
                key={m.run_id}
                className="border-t border-line hover:bg-surface/50 transition-colors"
              >
                <td className="px-4 py-3 font-mono">v{m.version}</td>
                <td className="px-4 py-3">
                  {m.finished_at
                    ? new Date(m.finished_at).toLocaleDateString()
                    : "—"}
                </td>
                <td className="px-4 py-3">{m.approved}</td>
                <td className="px-4 py-3">{m.rejected}</td>
                <td className="px-4 py-3">{formatPercent(m.approval_rate)}</td>
                <td className="px-4 py-3">{formatPercent(m.rejection_rate)}</td>
                <td className="px-4 py-3">
                  {m.is_active ? (
                    <span className="badge badge-green">активный</span>
                  ) : (
                    <span className="text-muted">{m.status}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card overflow-hidden p-0">
        <p className="section-label px-4 pt-4">
          Среднее время ответа по чатам
        </p>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left">
              <th className={TH}>Чат</th>
              <th className={TH}>Среднее время</th>
              <th className={TH}>Ответов</th>
            </tr>
          </thead>
          <tbody>
            {responseTime.length === 0 && (
              <tr>
                <td colSpan="3" className="text-muted px-4 py-4">
                  Данных пока нет.
                </td>
              </tr>
            )}
            {responseTime.slice(0, 20).map((r) => (
              <tr
                key={r.chat_id}
                className="border-t border-line hover:bg-surface/50 transition-colors"
              >
                <td className="px-4 py-3">
                  {r.chat_name || `chat ${r.chat_id}`}
                </td>
                <td className="px-4 py-3">{formatDuration(r.avg_seconds)}</td>
                <td className="px-4 py-3">{r.replies}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
