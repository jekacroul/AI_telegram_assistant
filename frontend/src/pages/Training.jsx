import React, { useEffect, useState } from "react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { api } from "../lib/api.js";

function formatEta(seconds) {
  if (!seconds || seconds <= 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}m ${s}s`;
}

const REASON_LABELS = {
  empty: "Пустой ответ",
  too_short: "Слишком короткий",
  echoes_incoming: "Повторяет сообщение",
  ai_phrase: "AI-фраза в начале",
  language_mismatch: "Другой язык",
  emoji_only: "Только эмодзи",
};

export default function Training() {
  const [status, setStatus] = useState(null);
  const [runs, setRuns] = useState([]);
  const [progress, setProgress] = useState(null);
  const [lossHistory, setLossHistory] = useState([]);
  const [datasetInfo, setDatasetInfo] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [quality, setQuality] = useState(null);

  const refresh = async () => {
    const [st, rs, q] = await Promise.all([
      api.trainingStatus(),
      api.trainingRuns(),
      api.qualityStats().catch(() => null),
    ]);
    setStatus(st);
    setRuns(rs);
    setQuality(q);
  };

  useEffect(() => {
    refresh();
    const ev = new EventSource("/api/training/progress");
    ev.onmessage = (e) => {
      try {
        const payload = JSON.parse(e.data);
        const data = payload.data || payload;
        setProgress(data);
        if (data.loss != null && data.step != null) {
          setLossHistory((prev) => {
            const next = [...prev, { step: data.step, loss: data.loss }];
            return next.slice(-200);
          });
        }
        if (data.phase === "done" || data.phase === "error" || data.phase === "cancelled") {
          refresh();
        }
      } catch {}
    };
    return () => ev.close();
  }, []);

  async function build() {
    setError("");
    try {
      const res = await api.buildDataset();
      setDatasetInfo(res);
      refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  async function start() {
    setBusy(true);
    setError("");
    setLossHistory([]);
    try {
      const res = await api.startTraining();
      if (!res.started) setError(res.reason || "не удалось запустить");
      refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    await api.cancelTraining();
  }

  async function activate(id) {
    await api.activateAdapter(id);
    refresh();
  }

  const canTrain = (status?.training_pairs || 0) >= 50 && !status?.running;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat title="Сообщений" value={status?.messages_collected ?? "—"} />
        <Stat title="Пар для обучения" value={status?.training_pairs ?? "—"} />
        <Stat
          title="Последний запуск"
          value={
            status?.last_run?.finished_at
              ? new Date(status.last_run.finished_at).toLocaleString()
              : status?.last_run?.status || "—"
          }
        />
        <Stat
          title="Активный адаптер"
          value={status?.active_adapter ? `v${status.active_adapter.version}` : "—"}
        />
      </div>

      {quality && (
        <div className="card">
          <div className="label">
            Отклонено фильтром качества: {quality.total_rejected} из{" "}
            {quality.total_generated}
          </div>
          {quality.total_generated > 0 && (
            <div className="mt-2 h-2 bg-white/10 rounded">
              <div
                className="h-2 bg-accent rounded"
                style={{
                  width: `${Math.min(
                    100,
                    (quality.total_rejected / quality.total_generated) * 100
                  )}%`,
                }}
              />
            </div>
          )}
          {quality.reasons && Object.keys(quality.reasons).length > 0 ? (
            <ul className="mt-3 text-sm space-y-1">
              {Object.entries(quality.reasons)
                .sort((a, b) => b[1] - a[1])
                .map(([reason, count]) => (
                  <li key={reason} className="flex justify-between">
                    <span>{REASON_LABELS[reason] || reason}</span>
                    <span className="text-muted">{count}</span>
                  </li>
                ))}
            </ul>
          ) : (
            <div className="text-muted text-sm mt-2">Отклонений ещё не было.</div>
          )}
        </div>
      )}

      <div className="card flex flex-wrap gap-2 items-center">
        <button className="btn-secondary" onClick={build}>
          Собрать датасет
        </button>
        <button
          className="btn-primary"
          disabled={!canTrain || busy}
          onClick={start}
          title={!canTrain ? "Нужно минимум 50 пар" : ""}
        >
          {status?.running ? "Идёт обучение..." : "Запустить fine-tuning"}
        </button>
        {status?.running && (
          <button className="btn-danger" onClick={cancel}>
            Отменить
          </button>
        )}
        {datasetInfo && datasetInfo.total_pairs > 0 && (
          <span className="text-sm text-muted ml-2">
            Датасет: {datasetInfo.total_pairs} пар, средняя длина ответа{" "}
            {datasetInfo.avg_output_len}
          </span>
        )}
        {error && <span className="text-bad text-sm ml-2">{error}</span>}
      </div>

      {progress && (
        <div className="card">
          <div className="flex flex-wrap gap-4 text-sm">
            <span>Phase: <b>{progress.phase}</b></span>
            {progress.phase === "training" && (
              <>
                <span>Epoch: {progress.epoch?.toFixed?.(2) ?? "—"}</span>
                <span>Step: {progress.step ?? "—"} / {progress.max_steps ?? "—"}</span>
                <span>Loss: {progress.loss?.toFixed?.(4) ?? "—"}</span>
                <span>ETA: {formatEta(progress.eta_seconds)}</span>
              </>
            )}
            {progress.phase === "done" && progress.final_loss != null && (
              <span>Final loss: <b>{progress.final_loss.toFixed(4)}</b></span>
            )}
            {progress.phase === "done" && progress.gguf_path && (
              <span className="text-good">
                GGUF: <code className="text-xs">{progress.gguf_path}</code>
              </span>
            )}
            {progress.phase === "done" && progress.gguf_path === null && (
              <span className="text-muted">
                GGUF не сконвертирован (настрой LLAMA_CPP_PATH в .env)
              </span>
            )}
          </div>
          {progress.phase === "training" && progress.max_steps > 0 && (
            <div className="mt-2 h-2 bg-white/10 rounded">
              <div
                className="h-2 bg-accent rounded"
                style={{
                  width: `${Math.min(
                    100,
                    ((progress.step || 0) / progress.max_steps) * 100
                  )}%`,
                }}
              />
            </div>
          )}
          {lossHistory.length > 1 && (
            <div className="h-48 mt-3">
              <ResponsiveContainer>
                <LineChart data={lossHistory}>
                  <XAxis dataKey="step" stroke="#9aa3b2" />
                  <YAxis stroke="#9aa3b2" />
                  <Tooltip
                    contentStyle={{ background: "#12151c", border: "1px solid #2a2f3a" }}
                  />
                  <Line
                    type="monotone"
                    dataKey="loss"
                    stroke="#5b8cff"
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      )}

      <div className="card">
        <div className="text-sm text-muted mb-2">История адаптеров</div>
        <table className="w-full text-sm">
          <thead className="text-muted">
            <tr className="text-left">
              <th className="py-1">Версия</th>
              <th>Дата</th>
              <th>Пар</th>
              <th>Loss</th>
              <th>Статус</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && (
              <tr>
                <td colSpan="6" className="text-muted py-3">
                  Запусков ещё не было.
                </td>
              </tr>
            )}
            {runs.map((r) => (
              <tr key={r.id} className="border-t border-white/5">
                <td className="py-2">v{r.version}</td>
                <td>
                  {r.started_at ? new Date(r.started_at).toLocaleString() : "—"}
                </td>
                <td>{r.pair_count}</td>
                <td>{r.final_loss?.toFixed?.(4) ?? "—"}</td>
                <td>
                  {r.is_active ? (
                    <span className="text-good">активный</span>
                  ) : (
                    r.status
                  )}
                </td>
                <td className="text-right">
                  {!r.is_active && r.status === "done" && (
                    <button
                      className="btn-secondary"
                      onClick={() => activate(r.id)}
                    >
                      Активировать
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Stat({ title, value }) {
  return (
    <div className="card">
      <div className="label">{title}</div>
      <div className="text-xl font-semibold mt-1 truncate">{value}</div>
    </div>
  );
}
