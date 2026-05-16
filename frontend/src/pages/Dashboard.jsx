import React, { useCallback, useEffect, useState } from "react";
import { RotateCcw } from "lucide-react";
import { api, streamEvents } from "../lib/api.js";
import { useTileLayout } from "../hooks/useTileLayout.js";
import TileGrid from "../components/TileGrid.jsx";
import MetricTile from "../components/tiles/MetricTile.jsx";
import ActivityTile from "../components/tiles/ActivityTile.jsx";
import ModelStatusTile from "../components/tiles/ModelStatusTile.jsx";
import MessageFeedTile from "../components/tiles/MessageFeedTile.jsx";
import ReplyPanelTile from "../components/tiles/ReplyPanelTile.jsx";
import ReplyModal from "../components/ReplyModal.jsx";

const REASON_LABELS = {
  too_short: "Слишком короткий",
  language_mismatch: "Не тот язык",
  identical_to_incoming: "Повтор входящего",
  ai_phrase: "AI-фраза",
  emoji_only: "Только emoji",
  no_variants: "Нет вариантов",
};

function shortDay(day) {
  if (!day) return "";
  const p = day.split("-");
  return p.length === 3 ? `${p[2]}.${p[1]}` : day;
}

function mean(arr) {
  return arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0;
}

export default function Dashboard() {
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
      api.whisperStatus(),
      api.pending(),
    ];
    const results = await Promise.allSettled(calls);
    const [status, messages, overview, quality, rag, activity, whisper, pending] =
      results.map((r) => (r.status === "fulfilled" ? r.value : null));
    setData({ status, messages, overview, quality, rag, activity, whisper, pending });
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
  const pendingCount = Array.isArray(data.pending) ? data.pending.length : 0;

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
            label="Сегодня получено"
            value={recvToday}
            accent="sky"
            series={recv14}
            expanded={expandedIds.has(id)}
            onToggleExpand={() => toggleExpand(id)}
          >
            <div>
              <div className="stat-row">
                <span className="stat-label">Макс за день</span>
                <span className="stat-value">{Math.max(0, ...recv14)}</span>
              </div>
              <div className="stat-row">
                <span className="stat-label">Среднее за 14 дней</span>
                <span className="stat-value">{mean(recv14).toFixed(1)}</span>
              </div>
              <div className="stat-row">
                <span className="stat-label">Всего за неделю</span>
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
            label="Отправлено ответов"
            value={sentToday}
            accent="emerald"
            series={sent14}
            expanded={expandedIds.has(id)}
            onToggleExpand={() => toggleExpand(id)}
          >
            <div>
              <div className="stat-row">
                <span className="stat-label">Всего отправлено</span>
                <span className="stat-value">{overview.sent ?? "—"}</span>
              </div>
              <div className="stat-row">
                <span className="stat-label">Одобрено вручную</span>
                <span className="stat-value">
                  {overview.approved_manual ?? "—"}
                </span>
              </div>
              <div className="stat-row">
                <span className="stat-label">Отвечено сообщений</span>
                <span className="stat-value">{overview.replied ?? "—"}</span>
              </div>
            </div>
          </MetricTile>
        );
      case "metrics-quality":
        return (
          <MetricTile
            dragHandleProps={dragHandleProps}
            label="Качество ответов"
            value={approvalPct}
            format={(v) => `${Math.round(v)}%`}
            accent="violet"
            expanded={expandedIds.has(id)}
            onToggleExpand={() => toggleExpand(id)}
          >
            <div>
              <div className="stat-row">
                <span className="stat-label">Сгенерировано</span>
                <span className="stat-value">
                  {quality.total_generated ?? "—"}
                </span>
              </div>
              <div className="stat-row">
                <span className="stat-label">Отклонено фильтром</span>
                <span className="stat-value">
                  {quality.total_rejected ?? "—"}
                </span>
              </div>
              {(quality.reasons || []).map((r) => (
                <div className="stat-row" key={r.reason}>
                  <span className="stat-label">
                    {REASON_LABELS[r.reason] || r.reason}
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
            label="RAG индексировано"
            value={rag.total_indexed || 0}
            accent="indigo"
            expanded={expandedIds.has(id)}
            onToggleExpand={() => toggleExpand(id)}
          >
            <div>
              <div className="stat-row">
                <span className="stat-label">Размер базы</span>
                <span className="stat-value">
                  {rag.collection_size_mb ?? 0} MB
                </span>
              </div>
              <div className="stat-row">
                <span className="stat-label">Модель</span>
                <span className="stat-value truncate ml-2">
                  {rag.model || "—"}
                </span>
              </div>
              <div className="stat-row">
                <span className="stat-label">Последняя индексация</span>
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
            ragOk={!!rag.enabled}
            whisperOk={!!data.whisper?.model_loaded}
            pendingCount={pendingCount}
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
          Перетаскивай за иконку, тяни края и угол — меняй размер
        </p>
        <button className="btn-ghost" onClick={reset} title="Сбросить раскладку">
          <RotateCcw size={14} />
          Сбросить раскладку
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
