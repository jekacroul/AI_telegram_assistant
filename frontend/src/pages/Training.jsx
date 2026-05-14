import React, { useEffect, useState } from "react";
import { api } from "../lib/api.js";

function formatEta(seconds) {
  if (!seconds || seconds <= 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}m ${s}s`;
}

const PHASE_LABELS = {
  starting: "запуск...",
  stopping_llama_server: "выгрузка llama-server",
  loading_tokenizer: "загрузка токенизатора",
  loading_model: "загрузка модели",
  model_loaded: "модель загружена",
  preparing_trainer: "подготовка тренера",
  training: "обучение",
  merging_and_exporting_gguf: "экспорт GGUF",
  loading_base: "загрузка fp16 базы",
  loading_adapter: "загрузка адаптера",
  merging: "слияние весов",
  saving_merged: "сохранение модели",
  converting_to_gguf: "конвертация в GGUF",
  quantizing: "квантизация",
  converting_lora_to_gguf: "конвертация LoRA в GGUF",
  copying_to_lm_studio: "копирование в LM Studio",
  done: "успех",
  error: "ошибка",
  cancelled: "отменено",
};

export default function Training() {
  const [status, setStatus] = useState(null);
  const [runs, setRuns] = useState([]);
  const [progress, setProgress] = useState(null);
  const [datasetInfo, setDatasetInfo] = useState(null);
  const [qualityStats, setQualityStats] = useState(null);
  const [voiceStats, setVoiceStats] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [runLogs, setRunLogs] = useState({});
  const [loadingLogId, setLoadingLogId] = useState(null);
  const [serverStatus, setServerStatus] = useState(null);

  const refresh = async () => {
    const [st, rs, qs] = await Promise.all([
      api.trainingStatus(),
      api.trainingRuns(),
      api.qualityStats(),
    ]);
    setStatus(st);
    setRuns(rs);
    setQualityStats(qs);
    try {
      const vs = await api.voiceStats();
      setVoiceStats(vs);
    } catch {
      // ignore
    }
    try {
      const srv = await api.llamaServerStatus();
      setServerStatus(srv);
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    refresh();
    const ev = new EventSource("/api/training/progress");
    ev.onmessage = (e) => {
      try {
        const payload = JSON.parse(e.data);
        const data = payload.data || payload;
        setProgress(data);
        if (data.phase === "done" || data.phase === "error" || data.phase === "cancelled") {
          refresh();
        }
      } catch {}
    };
    const srvPoll = setInterval(async () => {
      try {
        const srv = await api.llamaServerStatus();
        setServerStatus(srv);
      } catch {
        // ignore
      }
    }, 3000);
    return () => {
      ev.close();
      clearInterval(srvPoll);
    };
  }, []);

  async function serverStart() {
    setError("");
    try {
      const res = await api.llamaServerStart();
      if (!res.started) setError(res.reason || "не удалось запустить");
    } catch (e) {
      setError(e.message);
    }
  }

  async function serverStop() {
    setError("");
    try {
      await api.llamaServerStop();
    } catch (e) {
      setError(e.message);
    }
  }

  async function serverRestart() {
    setError("");
    try {
      await api.llamaServerRestart();
    } catch (e) {
      setError(e.message);
    }
  }

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

  async function deactivate() {
    await api.deactivateAdapter();
    refresh();
  }

  async function exportGguf(run) {
    setError("");
    try {
      const res = await api.exportGguf(run.id);
      if (!res.started) {
        setError(res.reason || "не удалось запустить конвертацию");
        return;
      }
      refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  async function exportLoraGguf(run) {
    setError("");
    try {
      const res = await api.exportLoraGguf(run.id);
      if (!res.started) {
        setError(res.reason || "не удалось запустить конвертацию");
        return;
      }
      refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  async function removeRun(run) {
    const confirmed = window.confirm(`Удалить v${run.version} из истории?`);
    if (!confirmed) return;
    setError("");
    try {
      await api.deleteTrainingRun(run.id);
      setRunLogs((prev) => {
        const next = { ...prev };
        delete next[run.id];
        return next;
      });
      refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  async function toggleLog(run) {
    if (runLogs[run.id]?.open) {
      setRunLogs((prev) => ({
        ...prev,
        [run.id]: { ...prev[run.id], open: false },
      }));
      return;
    }

    setLoadingLogId(run.id);
    setError("");
    try {
      const logInfo = runLogs[run.id]?.data || (await api.trainingRunErrorLog(run.id));
      setRunLogs((prev) => ({
        ...prev,
        [run.id]: { open: true, data: logInfo },
      }));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoadingLogId(null);
    }
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

      {voiceStats && (
        <div className="card">
          <div className="label">Голосовые сообщения</div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2 text-sm">
            <div>
              <div className="text-muted text-xs">Получено</div>
              <div className="text-lg font-semibold">{voiceStats.voice_received}</div>
            </div>
            <div>
              <div className="text-muted text-xs">Транскрибировано</div>
              <div className="text-lg font-semibold">{voiceStats.voice_transcribed}</div>
            </div>
            <div>
              <div className="text-muted text-xs">Низкая уверенность</div>
              <div className="text-lg font-semibold">{voiceStats.voice_low_confidence}</div>
            </div>
            <div>
              <div className="text-muted text-xs">Средняя уверенность</div>
              <div className="text-lg font-semibold">
                {voiceStats.avg_confidence != null
                  ? `${Math.round((voiceStats.avg_confidence || 0) * 100)}%`
                  : "—"}
              </div>
            </div>
          </div>
        </div>
      )}

      {serverStatus && (
        <div className="card">
          <div className="flex flex-wrap items-center gap-3">
            <div className="label">Llama Server</div>
            <span
              className={
                serverStatus.starting
                  ? "text-accent text-sm"
                  : serverStatus.stopping
                  ? "text-muted text-sm"
                  : serverStatus.running
                  ? "text-good text-sm"
                  : "text-muted text-sm"
              }
            >
              {serverStatus.starting
                ? "● запуск..."
                : serverStatus.stopping
                ? "● останавливается..."
                : serverStatus.running
                ? `● работает на :${serverStatus.port}`
                : "○ остановлен"}
            </span>
            {serverStatus.running && serverStatus.lora_path && (
              <span className="text-xs text-muted">
                LoRA: <code>{serverStatus.lora_path.split(/[\\/]/).pop()}</code>
              </span>
            )}
            {serverStatus.running && !serverStatus.lora_path && (
              <span className="text-xs text-muted">без адаптера (чистая база)</span>
            )}
            <div className="ml-auto flex gap-2">
              {!serverStatus.running && !serverStatus.starting && (
                <button className="btn-secondary" onClick={serverStart}>
                  Запустить
                </button>
              )}
              {serverStatus.running && (
                <button className="btn-secondary" onClick={serverRestart}>
                  Перезапустить
                </button>
              )}
              {serverStatus.running && (
                <button className="btn-danger" onClick={serverStop}>
                  Остановить
                </button>
              )}
            </div>
          </div>
          {!serverStatus.configured && (
            <div className="text-bad text-xs mt-2">
              Не задан LLAMA_BASE_MODEL_GGUF в .env — сервер запустить нельзя.
            </div>
          )}
          {serverStatus.last_error && !serverStatus.running && (
            <div className="text-bad text-xs mt-2">
              Ошибка: {serverStatus.last_error}
            </div>
          )}
        </div>
      )}

      <div className="card">
        <div className="label">Отклонено фильтром качества</div>
        <div className="text-xl font-semibold mt-1">
          {qualityStats
            ? `${qualityStats.total_rejected} из ${qualityStats.total_generated}`
            : "—"}
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-sm">
          {qualityStats?.reasons?.length ? (
            qualityStats.reasons.map((item) => (
              <span
                key={item.reason}
                className="rounded-full bg-white/10 px-3 py-1 text-muted"
              >
                {reasonLabel(item.reason)}: {item.count}
              </span>
            ))
          ) : (
            <span className="text-muted">Нет отклонений</span>
          )}
        </div>
      </div>

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
        {status?.active_adapter && (
          <button className="btn-secondary" onClick={deactivate}>
            Деактивировать адаптер
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

      {progress && (() => {
        const percent =
          progress.phase === "training" && progress.max_steps > 0
            ? Math.min(
                100,
                Math.max(
                  0,
                  ((progress.step || 0) / progress.max_steps) * 100
                )
              )
            : null;
        const isRunningPhase =
          progress.phase &&
          progress.phase !== "done" &&
          progress.phase !== "error" &&
          progress.phase !== "cancelled";
        const showIndeterminate = isRunningPhase && percent == null;
        return (
        <div className="card">
          <div className="flex flex-wrap gap-4 text-sm items-center">
            <span>
              Этап:{" "}
              <b>{PHASE_LABELS[progress.phase] || progress.phase}</b>
            </span>
            {progress.phase === "training" && (
              <>
                <span>Эпоха: {progress.epoch?.toFixed?.(2) ?? "—"}</span>
                <span>
                  Шаг: {progress.step ?? "—"} / {progress.max_steps ?? "—"}
                </span>
                <span>Loss: {progress.loss?.toFixed?.(4) ?? "—"}</span>
                <span>ETA: {formatEta(progress.eta_seconds)}</span>
                {percent != null && <span>{percent.toFixed(1)}%</span>}
              </>
            )}
            {progress.phase === "done" && progress.final_loss != null && (
              <span>Итоговый loss: <b>{progress.final_loss.toFixed(4)}</b></span>
            )}
            {progress.phase === "done" && progress.gguf_path && (
              <span className="text-good">
                GGUF: <code className="text-xs">{progress.gguf_path}</code>
                {progress.gguf_path.toLowerCase().includes(".lora.") && (
                  <span className="text-muted ml-2">
                    (LoRA-адаптер — в LM Studio подключай поверх базы через
                    Advanced → LoRA Adapters, не загружай как отдельную модель)
                  </span>
                )}
              </span>
            )}
            {progress.phase === "done" && progress.gguf_path === null && (
              <span className="text-bad">
                GGUF не сконвертирован: {progress.gguf_skip_reason || "настрой LLAMA_CPP_PATH в .env"}
              </span>
            )}
            {progress.phase === "error" && (
              <span className="text-bad">
                Ошибка: {progress.error || "неизвестная ошибка"}
              </span>
            )}
            {progress.phase === "cancelled" && (
              <span className="text-muted">Отменено пользователем</span>
            )}
          </div>
          {percent != null && (
            <div className="mt-3 h-2 bg-white/10 rounded">
              <div
                className="h-2 bg-accent rounded transition-all"
                style={{ width: `${percent}%` }}
              />
            </div>
          )}
          {showIndeterminate && (
            <div className="mt-3 progress-indeterminate" />
          )}
        </div>
        );
      })()}

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
              <React.Fragment key={r.id}>
                <tr className="border-t border-white/5">
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
                    <div className="flex justify-end gap-2 flex-wrap">
                      {!r.is_active && r.status === "done" && (
                        <button
                          className="btn-secondary"
                          onClick={() => activate(r.id)}
                        >
                          Активировать
                        </button>
                      )}
                      {r.is_active && (
                        <button className="btn-secondary" onClick={deactivate}>
                          Деактивировать
                        </button>
                      )}
                      {r.status === "done" && (
                        <button
                          className="btn-secondary"
                          disabled={status?.running}
                          onClick={() => exportLoraGguf(r)}
                          title={
                            status?.running
                              ? "Дождись окончания текущего процесса"
                              : "Сконвертировать LoRA в отдельный GGUF (~50-200 МБ). В LM Studio открой базовую модель → Load → Advanced → LoRA Adapters → добавь этот файл. Как самостоятельная модель НЕ загружается."
                          }
                        >
                          LoRA → GGUF
                        </button>
                      )}
                      {r.status === "done" && (
                        <button
                          className="btn-secondary"
                          disabled={status?.running}
                          onClick={() => exportGguf(r)}
                          title={
                            status?.running
                              ? "Дождись окончания текущего процесса"
                              : "Слить адаптер с базой fp16 и собрать единый GGUF (~12 ГБ, требует ~26 ГБ свободной RAM)"
                          }
                        >
                          Merge → GGUF
                        </button>
                      )}
                      {r.status !== "running" && (
                        <button
                          className="btn-secondary"
                          onClick={() => toggleLog(r)}
                        >
                          {loadingLogId === r.id
                            ? "Загрузка..."
                            : runLogs[r.id]?.open
                              ? "Скрыть лог"
                              : "Лог"}
                        </button>
                      )}
                      {(r.status === "failed" || r.status === "cancelled") &&
                        !r.is_active && (
                        <button
                          className="btn-danger"
                          onClick={() => removeRun(r)}
                        >
                          Удалить
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
                {runLogs[r.id]?.open && (
                  <tr className="border-t border-white/5">
                    <td colSpan="6" className="pb-3">
                      <div className="mt-2 rounded-lg border border-white/10 bg-black/30 p-3">
                        <div className="flex flex-wrap gap-2 items-center text-xs text-muted mb-2">
                          <span>Лог запуска v{r.version}</span>
                          {runLogs[r.id].data?.log_path && (
                            <code className="break-all">{runLogs[r.id].data.log_path}</code>
                          )}
                        </div>
                        {runLogs[r.id].data?.error && (
                          <div
                            className={`text-sm mb-2 ${
                              r.status === "failed" ? "text-bad" : "text-muted"
                            }`}
                          >
                            {r.status === "failed" ? "Ошибка: " : "Последняя строка: "}
                            {runLogs[r.id].data.error}
                          </div>
                        )}
                        {runLogs[r.id].data?.excerpt ? (
                          <pre className="max-h-80 overflow-auto whitespace-pre-wrap text-xs text-white/90">
                            {runLogs[r.id].data.excerpt}
                          </pre>
                        ) : (
                          <div className="text-muted text-sm">
                            Не удалось найти лог запуска.
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function reasonLabel(reason) {
  const labels = {
    too_short: "Слишком короткий ответ",
    language_mismatch: "Другой язык",
    identical_to_incoming: "Повтор входящего",
    ai_phrase: "AI-фраза",
    emoji_only: "Только emoji",
    no_variants: "Нет вариантов",
  };
  return labels[reason] || reason;
}

function Stat({ title, value }) {
  return (
    <div className="card">
      <div className="label">{title}</div>
      <div className="text-xl font-semibold mt-1 truncate">{value}</div>
    </div>
  );
}
