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

const tooltipStyle = {
  background: "#12151c",
  border: "1px solid #2a2f3a",
};

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

function TopChatTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const item = payload[0].payload;
  return (
    <div style={tooltipStyle} className="px-3 py-2 text-sm shadow-lg">
      <div className="font-semibold text-white">{item.name}</div>
      <div className="text-muted">Ник: {formatUsername(item.username)}</div>
      <div className="text-muted">Сообщений: {item.count}</div>
    </div>
  );
}

function Card({ title, value, hint }) {
  return (
    <div className="card">
      <div className="label">{title}</div>
      <div className="text-2xl font-semibold mt-1">{value}</div>
      {hint && <div className="text-xs text-muted mt-1">{hint}</div>}
    </div>
  );
}

export default function Stats() {
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
    <div className="space-y-4">
      {error && (
        <div className="card border-bad/40 text-sm text-bad">{error}</div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card
          title="Получено"
          value={overview?.received ?? "—"}
          hint="Входящих сообщений"
        />
        <Card
          title="Отправлено"
          value={overview?.sent ?? "—"}
          hint={`Отвечено: ${overview?.replied ?? 0}`}
        />
        <Card
          title="Одобрено вручную"
          value={overview?.approved_manual ?? "—"}
          hint={`Approval rate: ${formatPercent(overview?.approval_rate)}`}
        />
        <Card
          title="Отклонено фильтром"
          value={overview?.rejected ?? "—"}
          hint="Плохой фидбек"
        />
      </div>

      <div className="card">
        <div className="label mb-2">Активность за 30 дней</div>
        <div className="h-64">
          <ResponsiveContainer>
            <LineChart data={activityChartData}>
              <CartesianGrid stroke="#1f2430" strokeDasharray="3 3" />
              <XAxis dataKey="label" stroke="#9aa3b2" />
              <YAxis stroke="#9aa3b2" allowDecimals={false} />
              <Tooltip contentStyle={tooltipStyle} />
              <Line
                type="monotone"
                dataKey="received"
                name="Получено"
                stroke="#5b8cff"
                dot={false}
                isAnimationActive={false}
              />
              <Line
                type="monotone"
                dataKey="sent"
                name="Отправлено"
                stroke="#22c55e"
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="card">
        <div className="label mb-2">Топ чатов по числу сообщений</div>
        <div className="h-64">
          <ResponsiveContainer>
            <BarChart
              data={topChatsData}
              layout="vertical"
              margin={{ left: 8, right: 12 }}
            >
              <CartesianGrid stroke="#1f2430" strokeDasharray="3 3" />
              <XAxis type="number" stroke="#9aa3b2" allowDecimals={false} />
              <YAxis
                type="category"
                dataKey="name"
                stroke="#9aa3b2"
                width={topChatsAxisWidth}
                interval={0}
              />
              <Tooltip
                content={<TopChatTooltip />}
                cursor={{ fill: "rgba(91, 140, 255, 0.12)" }}
                shared={false}
              />
              <Bar dataKey="count" fill="#5b8cff" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="card">
        <div className="label mb-2">Качество по версиям модели</div>
        <table className="w-full text-sm">
          <thead className="text-muted">
            <tr className="text-left">
              <th className="py-1">Версия</th>
              <th>Завершено</th>
              <th>Одобрено</th>
              <th>Отклонено</th>
              <th>Approval</th>
              <th>Rejection</th>
              <th>Статус</th>
            </tr>
          </thead>
          <tbody>
            {modelQuality.length === 0 && (
              <tr>
                <td colSpan="7" className="text-muted py-3">
                  Данных пока нет.
                </td>
              </tr>
            )}
            {modelQuality.map((m) => (
              <tr key={m.run_id} className="border-t border-white/5">
                <td className="py-2">v{m.version}</td>
                <td>
                  {m.finished_at
                    ? new Date(m.finished_at).toLocaleDateString()
                    : "—"}
                </td>
                <td>{m.approved}</td>
                <td>{m.rejected}</td>
                <td>{formatPercent(m.approval_rate)}</td>
                <td>{formatPercent(m.rejection_rate)}</td>
                <td>
                  {m.is_active ? (
                    <span className="text-good">активный</span>
                  ) : (
                    m.status
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="label mb-2">Среднее время ответа по чатам</div>
        <table className="w-full text-sm">
          <thead className="text-muted">
            <tr className="text-left">
              <th className="py-1">Чат</th>
              <th>Среднее время</th>
              <th>Ответов</th>
            </tr>
          </thead>
          <tbody>
            {responseTime.length === 0 && (
              <tr>
                <td colSpan="3" className="text-muted py-3">
                  Данных пока нет.
                </td>
              </tr>
            )}
            {responseTime.slice(0, 20).map((r) => (
              <tr key={r.chat_id} className="border-t border-white/5">
                <td className="py-2">{r.chat_name || `chat ${r.chat_id}`}</td>
                <td>{formatDuration(r.avg_seconds)}</td>
                <td>{r.replies}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
