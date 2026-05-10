import { useEffect, useMemo, useRef, useState } from "react";
import { Card, Button, Input } from "../components/Card.jsx";
import { api, streamTrainingProgress } from "../api.js";

function formatEta(seconds) {
  if (!seconds || seconds <= 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function LossSparkline({ history }) {
  if (!history || history.length === 0) return null;
  const w = 320;
  const h = 60;
  const losses = history.map((p) => p.loss);
  const min = Math.min(...losses);
  const max = Math.max(...losses);
  const range = max - min || 1;
  const points = history
    .map((p, i) => {
      const x = (i / (history.length - 1 || 1)) * w;
      const y = h - ((p.loss - min) / range) * h;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg width={w} height={h} className="rounded bg-slate-950">
      <polyline
        points={points}
        fill="none"
        stroke="#3b82f6"
        strokeWidth="1.5"
      />
    </svg>
  );
}

export default function Training() {
  const [status, setStatus] = useState(null);
  const [progress, setProgress] = useState(null);
  const [datasetStats, setDatasetStats] = useState(null);
  const [epochs, setEpochs] = useState(3);
  const [batchSize, setBatchSize] = useState(4);
  const [error, setError] = useState(null);
  const esRef = useRef(null);

  async function refresh() {
    try {
      const s = await api.trainingStatus();
      setStatus(s);
      setProgress(s.progress);
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    refresh();
    esRef.current = streamTrainingProgress((p) => setProgress(p));
    const id = setInterval(refresh, 6000);
    return () => {
      esRef.current?.close();
      clearInterval(id);
    };
  }, []);

  async function buildDataset() {
    setError(null);
    try {
      const stats = await api.trainingDatasetBuild();
      setDatasetStats(stats);
      await refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  async function startTraining() {
    setError(null);
    try {
      const res = await api.trainingStart({
        epochs: Number(epochs),
        batch_size: Number(batchSize),
      });
      if (!res.started) setError(res.message || "Could not start training");
      await refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  async function cancelTraining() {
    await api.trainingCancel();
    await refresh();
  }

  const isRunning = progress?.status === "running" || progress?.status === "preparing";
  const stepPct = useMemo(() => {
    if (!progress?.total_steps) return 0;
    return Math.min(100, Math.round((progress.current_step / progress.total_steps) * 100));
  }, [progress]);

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-3">
        <Card title="Total messages collected">
          <p className="text-2xl font-semibold text-white">
            {status?.runs?.[0]?.pair_count ?? "—"}
          </p>
        </Card>
        <Card title="Training pairs available">
          <p className="text-2xl font-semibold text-white">
            {status?.total_training_pairs ?? "—"}
          </p>
          <p className="text-xs text-slate-400">
            min to start: {status?.min_pairs_for_training ?? 50}
          </p>
        </Card>
        <Card title="Last fine-tune">
          <p className="text-sm text-slate-200">
            {status?.last_run?.finished_at
              ? new Date(status.last_run.finished_at).toLocaleString()
              : "never"}
          </p>
          <p className="text-xs text-slate-400">
            v{status?.last_run?.version ?? "—"} · loss{" "}
            {status?.last_run?.final_loss?.toFixed?.(3) ?? "—"}
          </p>
        </Card>
      </div>

      {error && (
        <div className="rounded border border-rose-700 bg-rose-900/40 px-3 py-2 text-sm text-rose-200">
          {error}
        </div>
      )}

      <Card
        title="Dataset"
        action={<Button variant="ghost" onClick={buildDataset}>Build dataset</Button>}
      >
        {datasetStats ? (
          <div className="grid grid-cols-2 gap-2 text-sm text-slate-300 md:grid-cols-4">
            <div>New pairs: <strong>{datasetStats.new_pairs}</strong></div>
            <div>Total: <strong>{datasetStats.total_pairs}</strong></div>
            <div>Avg reply words: <strong>{datasetStats.avg_reply_words}</strong></div>
            <div>Range: <strong>{datasetStats.date_min?.slice(0, 10)} – {datasetStats.date_max?.slice(0, 10)}</strong></div>
          </div>
        ) : (
          <p className="text-sm text-slate-400">Click "Build dataset" to scan recent messages and create training pairs.</p>
        )}
      </Card>

      <Card title="Fine-tuning">
        <div className="mb-3 grid gap-3 md:grid-cols-3">
          <label className="text-sm text-slate-300">
            Epochs
            <Input
              type="number"
              min={1}
              max={20}
              value={epochs}
              onChange={(e) => setEpochs(e.target.value)}
            />
          </label>
          <label className="text-sm text-slate-300">
            Batch size
            <Input
              type="number"
              min={1}
              max={32}
              value={batchSize}
              onChange={(e) => setBatchSize(e.target.value)}
            />
          </label>
          <div className="flex items-end gap-2">
            <Button onClick={startTraining} disabled={isRunning}>
              {isRunning ? "Running…" : "Start fine-tuning"}
            </Button>
            <Button onClick={cancelTraining} variant="danger" disabled={!isRunning}>
              Cancel
            </Button>
          </div>
        </div>

        <div className="space-y-3">
          <div className="flex items-center justify-between text-sm text-slate-300">
            <span>Status: <strong>{progress?.status ?? "idle"}</strong></span>
            <span>
              Epoch {progress?.current_epoch ?? 0}/{progress?.total_epochs ?? 0} ·
              Step {progress?.current_step ?? 0}/{progress?.total_steps ?? 0}
            </span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded bg-slate-800">
            <div
              className="h-full bg-brand-500 transition-all"
              style={{ width: `${stepPct}%` }}
            />
          </div>
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>Loss: {progress?.last_loss?.toFixed?.(4) ?? "—"}</span>
            <span>ETA: {formatEta(progress?.eta_seconds)}</span>
          </div>
          <LossSparkline history={progress?.history} />
          {progress?.message && (
            <p className="text-xs text-slate-400">{progress.message}</p>
          )}
        </div>
      </Card>

      <Card title="Adapter history">
        {!status?.runs?.length ? (
          <p className="text-sm text-slate-400">No training runs yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs uppercase text-slate-500">
              <tr>
                <th className="py-1 text-left">Version</th>
                <th className="py-1 text-left">Date</th>
                <th className="py-1 text-left">Pairs</th>
                <th className="py-1 text-left">Loss</th>
                <th className="py-1 text-left">Status</th>
                <th className="py-1" />
              </tr>
            </thead>
            <tbody>
              {status.runs.map((r) => (
                <tr key={r.id} className="border-t border-slate-800">
                  <td className="py-1.5">v{r.version}</td>
                  <td className="py-1.5 text-slate-400">
                    {r.finished_at ? new Date(r.finished_at).toLocaleString() : "—"}
                  </td>
                  <td className="py-1.5">{r.pair_count}</td>
                  <td className="py-1.5">{r.final_loss?.toFixed?.(3) ?? "—"}</td>
                  <td className="py-1.5">{r.status}</td>
                  <td className="py-1.5 text-right">
                    <Button
                      variant="ghost"
                      onClick={() => api.trainingActivate(r.version).then(refresh)}
                      disabled={!r.adapter_path}
                    >
                      Activate
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
