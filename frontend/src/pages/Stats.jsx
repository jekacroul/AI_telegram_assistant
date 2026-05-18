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
import {
  BarChart2,
  Activity,
  Inbox,
  Send,
  ThumbsUp,
  ThumbsDown,
  Award,
  Timer,
} from "lucide-react";
import { useTheme } from "../hooks/useTheme.js";
import { useLang } from "../hooks/useLang.js";
import { chartColors } from "../lib/colors.js";
import Page from "../components/Page.jsx";
import Tile from "../components/Tile.jsx";

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

function formatDuration(seconds, t) {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  if (seconds < 60) return t("stats.durSeconds", { value: Math.round(seconds) });
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return t("stats.durMinutes", { m, s });
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return t("stats.durHours", { h, m: mm });
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
  const { t } = useLang();
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
        <div className="text-muted">
          {t("stats.username", { value: formatUsername(item.username) })}
        </div>
        <div className="text-muted">
          {t("stats.messagesCount", { count: item.count })}
        </div>
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
    <Page className="space-y-6">
      {error && (
        <div
          className="tile flex items-center gap-2 text-sm
                     text-rose-600 dark:text-rose-300
                     border-rose-300 dark:border-rose-900/60"
        >
          {error}
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          label={t("stats.received")}
          value={overview?.received ?? "—"}
          hint={t("stats.receivedHint")}
          icon={Inbox}
          accent="sky"
        />
        <MetricCard
          label={t("stats.sent")}
          value={overview?.sent ?? "—"}
          hint={t("stats.sentHint", { count: overview?.replied ?? 0 })}
          icon={Send}
          accent="emerald"
        />
        <MetricCard
          label={t("stats.approvedManual")}
          value={overview?.approved_manual ?? "—"}
          hint={t("stats.approvalHint", {
            rate: formatPercent(overview?.approval_rate),
          })}
          icon={ThumbsUp}
          accent="violet"
        />
        <MetricCard
          label={t("stats.rejected")}
          value={overview?.rejected ?? "—"}
          hint={t("stats.rejectedHint")}
          icon={ThumbsDown}
          accent="rose"
        />
      </div>

      <Tile title={t("stats.activity30")} icon={Activity}>
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
                name={t("stats.received")}
                stroke={colors.line1}
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
              <Line
                type="monotone"
                dataKey="sent"
                name={t("stats.sent")}
                stroke={colors.line2}
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </Tile>

      <Tile title={t("stats.topChats")} icon={BarChart2}>
        {topChatsData.length === 0 ? (
          <EmptyState
            icon={BarChart2}
            title={t("stats.noData")}
            description={t("stats.noDataDesc")}
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
      </Tile>

      <div className="tile overflow-hidden p-0">
        <div className="flex items-center gap-2 px-4 pt-4 pb-3">
          <Award
            size={14}
            className="text-indigo-500 dark:text-indigo-400 flex-shrink-0"
          />
          <span className="tile-label mb-0">
            {t("stats.qualityByVersion")}
          </span>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left">
              <th className={TH}>{t("stats.thVersion")}</th>
              <th className={TH}>{t("stats.thFinished")}</th>
              <th className={TH}>{t("stats.thApproved")}</th>
              <th className={TH}>{t("stats.thRejected")}</th>
              <th className={TH}>{t("stats.thApproval")}</th>
              <th className={TH}>{t("stats.thRejection")}</th>
              <th className={TH}>{t("stats.thStatus")}</th>
            </tr>
          </thead>
          <tbody>
            {modelQuality.length === 0 && (
              <tr>
                <td colSpan="7" className="text-muted px-4 py-4">
                  {t("stats.noRows")}
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
                    <span className="badge badge-green">
                      {t("stats.active")}
                    </span>
                  ) : (
                    <span className="text-muted">{m.status}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="tile overflow-hidden p-0">
        <div className="flex items-center gap-2 px-4 pt-4 pb-3">
          <Timer
            size={14}
            className="text-indigo-500 dark:text-indigo-400 flex-shrink-0"
          />
          <span className="tile-label mb-0">
            {t("stats.avgResponseTime")}
          </span>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left">
              <th className={TH}>{t("stats.thChat")}</th>
              <th className={TH}>{t("stats.thAvgTime")}</th>
              <th className={TH}>{t("stats.thReplies")}</th>
            </tr>
          </thead>
          <tbody>
            {responseTime.length === 0 && (
              <tr>
                <td colSpan="3" className="text-muted px-4 py-4">
                  {t("stats.noRows")}
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
                <td className="px-4 py-3">{formatDuration(r.avg_seconds, t)}</td>
                <td className="px-4 py-3">{r.replies}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Page>
  );
}
