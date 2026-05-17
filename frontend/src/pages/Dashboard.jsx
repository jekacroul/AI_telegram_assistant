import React, { useCallback, useEffect, useState } from "react";
import { RotateCcw } from "lucide-react";
import { api, streamEvents } from "../lib/api.js";
import { useTileLayout } from "../hooks/useTileLayout.js";
import TileGrid from "../components/TileGrid.jsx";
import MetricTile from "../components/tiles/MetricTile.jsx";
import ActivityTile from "../components/tiles/ActivityTile.jsx";
import ModelStatusTile from "../components/tiles/ModelStatusTile.jsx";
import LlamaServerTile from "../components/tiles/LlamaServerTile.jsx";
import VectorMemoryTile from "../components/tiles/VectorMemoryTile.jsx";
import MessageFeedTile from "../components/tiles/MessageFeedTile.jsx";
import ReplyPanelTile from "../components/tiles/ReplyPanelTile.jsx";
import ReplyModal from "../components/ReplyModal.jsx";
import { useLang } from "../hooks/useLang.js";

function shortDay(day) {
  if (!day) return "";
  const p = day.split("-");
  return p.length === 3 ? `${p[2]}.${p[1]}` : day;
}

function mean(arr) {
  return arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0;
}

export default function Dashboard() {
  const { t } = useLang();
  const { order, reorder, sizes, setSize, reset } = useTileLayout();
  const [expandedIds, setExpandedIds] = useState(() => new Set());
  const [data, setData] = useState({});
  const [active, setActive] = useState(null);

  const refresh = useCallback(async () => {
    const calls = [
      api.status(),
      api.recent(50),
      api.statsOverview(),
      api.qualityStats(),
      api.ragStatus(),
      api.statsActivity(),
      api.pending(),
    ];
    const results = await Promise.allSettled(calls);
    const [status, messages, overview, quality, rag, activity, pending] =
      results.map((r) => (r.status === "fulfilled" ? r.value : null));
    setData({ status, messages, overview, quality, rag, activity, pending });
  }, []);

  useEffect(() => {
    refresh();
    const stop = streamEvents("/api/stream/events", refresh);
    const id = setInterval(refresh, 30000);
    return () => {
      stop();
      clearInterval(id);
    };
  }, [refresh]);

  function toggleExpand(id) {
    setExpandedIds((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function toggleAuto() {
    await api.saveSettings({ auto_reply: !data.status?.auto_reply });
    refresh();
  }

  const activity = Array.isArray(data.activity) ? data.activity : [];
  const last14 = activity.slice(-14);
  const recv14 = last14.map((r) => r.received || 0);
  const sent14 = last14.map((r) => r.sent || 0);
  const bars = activity
    .slice(-24)
    .map((r) => ({ label: shortDay(r.day), value: r.received || 0 }));

  const overview = data.overview || {};
  const quality = data.quality || {};
  const rag = data.rag || {};
  const status = data.status || {};
  const messages = Array.isArray(data.messages) ? data.messages : [];
  const pending = Array.isArray(data.pending?.messages) ? data.pending.messages : [];
  const pendingCount = data.pending?.count ?? pending.length;

  const recvToday = recv14[recv14.length - 1] || 0;
  const sentToday = sent14[sent14.length - 1] || 0;
  const approvalPct =
    overview.approval_rate != null ? Math.round(overview.approval_rate * 100) : 0;

  function renderTile(id, dragHandleProps) {
    switch (id) {
      case "metrics-received":
        return (
          <MetricTile
            dragHandleProps={dragHandleProps}
            label={t("dashboard.receivedToday")}
            value={recvToday}
            accent="sky"
            series={recv14}
            expanded={expandedIds.has(id)}
            onToggleExpand={() => toggleExpand(id)}
          >
            <div>
              <div className="stat-row">
                <span className="stat-label">{t("dashboard.maxPerDay")}</span>
                <span className="stat-value">{Math.max(0, ...recv14)}</span>
              </div>
              <div className="stat-row">
                <span className="stat-label">{t("dashboard.avg14")}</span>
                <span className="stat-value">{mean(recv14).toFixed(1)}</span>
              </div>
              <div className="stat-row">
                <span className="stat-label">{t("dashboard.weekTotal")}</span>
                <span className="stat-value">
                  {recv14.slice(-7).reduce((a, b) => a + b, 0)}
                </span>
              </div>
            </div>
          </MetricTile>
        );
      case "metrics-sent":
        return (
          <MetricTile
            dragHandleProps={dragHandleProps}
            label={t("dashboard.repliesSent")}
            value={sentToday}
            accent="emerald"
            series={sent14}
            expanded={expandedIds.has(id)}
            onToggleExpand={() => toggleExpand(id)}
          >
            <div>
              <div className="stat-row">
                <span className="stat-label">{t("dashboard.totalSent")}</span>
                <span className="stat-value">{overview.sent ?? "—"}</span>
              </div>
              <div className="stat-row">
                <span className="stat-label">
                  {t("dashboard.approvedManual")}
                </span>
                <span className="stat-value">
                  {overview.approved_manual ?? "—"}
                </span>
              </div>
              <div className="stat-row">
                <span className="stat-label">
                  {t("dashboard.messagesReplied")}
                </span>
                <span className="stat-value">{overview.replied ?? "—"}</span>
              </div>
            </div>
          </MetricTile>
        );
      case "metrics-quality":
        return (
          <MetricTile
            dragHandleProps={dragHandleProps}
            label={t("dashboard.replyQuality")}
            value={approvalPct}
            format={(v) => `${Math.round(v)}%`}
            accent="violet"
            expanded={expandedIds.has(id)}
            onToggleExpand={() => toggleExpand(id)}
          >
            <div>
              <div className="stat-row">
                <span className="stat-label">{t("dashboard.generated")}</span>
                <span className="stat-value">
                  {quality.total_generated ?? "—"}
                </span>
              </div>
              <div className="stat-row">
                <span className="stat-label">
                  {t("dashboard.rejectedByFilter")}
                </span>
                <span className="stat-value">
                  {quality.total_rejected ?? "—"}
                </span>
              </div>
              {(quality.reasons || []).map((r) => (
                <div className="stat-row" key={r.reason}>
                  <span className="stat-label">
                    {t(`dashboard.reasons.${r.reason}`)}
                  </span>
                  <span className="stat-value">{r.count}</span>
                </div>
              ))}
            </div>
          </MetricTile>
        );
      case "metrics-rag":
        return (
          <MetricTile
            dragHandleProps={dragHandleProps}
            label={t("dashboard.ragIndexed")}
            value={rag.total_indexed || 0}
            accent="indigo"
            expanded={expandedIds.has(id)}
            onToggleExpand={() => toggleExpand(id)}
          >
            <div>
              <div className="stat-row">
                <span className="stat-label">{t("dashboard.dbSize")}</span>
                <span className="stat-value">
                  {rag.collection_size_mb ?? 0} MB
                </span>
              </div>
              <div className="stat-row">
                <span className="stat-label">{t("dashboard.model")}</span>
                <span className="stat-value truncate ml-2">
                  {rag.model || "—"}
                </span>
              </div>
              <div className="stat-row">
                <span className="stat-label">
                  {t("dashboard.lastIndexing")}
                </span>
                <span className="stat-value">
                  {rag.last_indexed_at
                    ? new Date(rag.last_indexed_at).toLocaleDateString()
                    : "—"}
                </span>
              </div>
            </div>
          </MetricTile>
        );
      case "activity-chart":
        return <ActivityTile dragHandleProps={dragHandleProps} bars={bars} />;
      case "model-status":
        return <ModelStatusTile dragHandleProps={dragHandleProps} />;
      case "llama-server":
        return <LlamaServerTile dragHandleProps={dragHandleProps} />;
      case "vector-memory":
        return <VectorMemoryTile dragHandleProps={dragHandleProps} />;
      case "message-feed":
        return (
          <MessageFeedTile
            dragHandleProps={dragHandleProps}
            messages={messages}
            onSelect={!status.auto_reply ? setActive : undefined}
          />
        );
      case "reply-panel":
        return (
          <ReplyPanelTile
            dragHandleProps={dragHandleProps}
            status={status}
            messages={pending}
            count={pendingCount}
            onSelect={setActive}
            onToggleAuto={toggleAuto}
          />
        );
      default:
        return null;
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-zinc-400 dark:text-slate-500">
          {t("dashboard.hint")}
        </p>
        <button
          className="btn-ghost"
          onClick={reset}
          title={t("dashboard.resetLayout")}
        >
          <RotateCcw size={14} />
          {t("dashboard.resetLayout")}
        </button>
      </div>

      <TileGrid
        order={order}
        sizes={sizes}
        expandedIds={expandedIds}
        onReorder={reorder}
        onResize={setSize}
        renderTile={renderTile}
      />

      {active && (
        <ReplyModal
          message={active}
          onClose={() => setActive(null)}
          onSent={refresh}
        />
      )}
    </div>
  );
}
