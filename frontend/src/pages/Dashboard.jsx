import React, { useCallback, useEffect, useState } from "react";
import { api, streamEvents } from "../lib/api.js";
import MessageCard from "../components/MessageCard.jsx";
import ReplyModal from "../components/ReplyModal.jsx";
import StatusDot from "../components/StatusDot.jsx";

export default function Dashboard() {
  const [status, setStatus] = useState({});
  const [messages, setMessages] = useState([]);
  const [active, setActive] = useState(null);
  const [editingFeedback, setEditingFeedback] = useState(null);
  const [correction, setCorrection] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [s, m] = await Promise.all([api.status(), api.recent(50)]);
      setStatus(s);
      setMessages(m);
    } catch (e) {
      console.error(e);
    }
  }, []);

  useEffect(() => {
    refresh();
    const stop = streamEvents("/api/stream/events", () => {
      refresh();
    });
    const id = setInterval(refresh, 30000);
    return () => {
      stop();
      clearInterval(id);
    };
  }, [refresh]);

  async function toggleAuto() {
    await api.saveSettings({ auto_reply: !status.auto_reply });
    refresh();
  }

  async function onFeedback(msg, kind) {
    if (kind === "good") {
      await api.feedback(msg.id, "good");
      refresh();
    } else {
      setEditingFeedback(msg);
      setCorrection(msg.reply_text || "");
    }
  }

  async function submitCorrection() {
    if (!editingFeedback) return;
    await api.feedback(editingFeedback.id, "bad", correction);
    setEditingFeedback(null);
    setCorrection("");
    refresh();
  }

  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-6">
          <StatusDot ok={!!status.llm} label="LLM" />
          <StatusDot ok={!!status.bot} label="Bot" />
          <StatusDot ok={!!status.db} label="DB" />
          <div className="text-xs text-muted">
            апдейтов: <span className="text-white">{status.update_count ?? 0}</span>
            {status.last_update_at && (
              <>
                {" · последний: "}
                <span className="text-white">
                  {new Date(status.last_update_at).toLocaleTimeString()}
                </span>
                {status.last_update_kind && (
                  <span className="text-muted"> ({status.last_update_kind})</span>
                )}
              </>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted">Авто-ответ</span>
          <button
            onClick={toggleAuto}
            className={`btn ${
              status.auto_reply ? "bg-good text-white" : "bg-white/10 text-muted"
            }`}
          >
            {status.auto_reply ? "ON" : "OFF"}
          </button>
        </div>
      </div>

      {status.last_error && (
        <div className="card border-bad/40 text-sm text-bad">
          Последняя ошибка: {status.last_error}
        </div>
      )}

      {status.bot && (status.update_count ?? 0) === 0 && (
        <div className="card text-sm text-muted">
          ⚠️ Апдейтов от Telegram пока не приходило. Проверь:
          <ul className="list-disc ml-5 mt-1 space-y-0.5">
            <li>webhook зарегистрирован при старте через start.py (Cloudflare tunnel)</li>
            <li>в Telegram → Settings → Business → Chatbots бот подключён, и в Manage messages выбраны нужные чаты</li>
            <li>backend доступен по HTTPS снаружи (ngrok/cloudflared)</li>
          </ul>
        </div>
      )}

      <div className="text-sm text-muted">
        Лента сообщений ({messages.length})
      </div>

      <div className="grid gap-3">
        {messages.length === 0 && (
          <div className="card text-muted text-sm">
            Сообщений пока нет. Отправь что-нибудь боту в Telegram.
          </div>
        )}
        {messages.map((m) => (
          <MessageCard
            key={m.id}
            msg={m}
            onReply={!status.auto_reply ? setActive : undefined}
            onFeedback={status.auto_reply ? onFeedback : undefined}
          />
        ))}
      </div>

      {active && (
        <ReplyModal
          message={active}
          onClose={() => setActive(null)}
          onSent={refresh}
        />
      )}

      {editingFeedback && (
        <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4">
          <div className="card w-full max-w-xl">
            <div className="label">Исправь ответ</div>
            <div className="text-sm mt-1 mb-3 text-muted whitespace-pre-wrap">
              На сообщение: {editingFeedback.text}
            </div>
            <textarea
              className="input min-h-[100px]"
              value={correction}
              onChange={(e) => setCorrection(e.target.value)}
            />
            <div className="flex justify-end gap-2 mt-3">
              <button
                className="btn-secondary"
                onClick={() => setEditingFeedback(null)}
              >
                Отмена
              </button>
              <button className="btn-primary" onClick={submitCorrection}>
                Отправить заново
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
