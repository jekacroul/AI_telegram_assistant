import React, { useCallback, useEffect, useState } from "react";
import { api, streamEvents } from "../lib/api.js";
import MessageCard from "../components/MessageCard.jsx";
import ReplyModal from "../components/ReplyModal.jsx";
import StatusPill from "../components/StatusPill.jsx";
import EmptyState from "../components/EmptyState.jsx";
import { MessageSquare } from "lucide-react";

export default function Dashboard() {
  const [status, setStatus] = useState({});
  const [messages, setMessages] = useState([]);
  const [active, setActive] = useState(null);
  const [editingFeedback, setEditingFeedback] = useState(null);
  const [correction, setCorrection] = useState("");
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [s, m] = await Promise.all([api.status(), api.recent(50)]);
      setStatus(s);
      setMessages(m);
    } catch (e) {
      console.error(e);
    } finally {
      setLoaded(true);
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
    <div className="space-y-6">
      <div className="flex items-center gap-2 flex-wrap">
        <StatusPill ok={!!status.llm} label="LM Studio" />
        <StatusPill ok={!!status.bot} label="Бот" />
        <StatusPill ok={!!status.db} label="База данных" />
        <button
          onClick={toggleAuto}
          className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full border
            text-xs font-medium transition-colors duration-100
            ${
              status.auto_reply
                ? "border-accent bg-accent text-accent-fg"
                : "border-line text-muted hover:text-fg"
            }`}
        >
          Авто-ответ {status.auto_reply ? "ВКЛ" : "ВЫКЛ"}
        </button>
        {status.update_count != null && (
          <span className="text-xs text-muted ml-auto">
            апдейтов: <span className="text-fg">{status.update_count}</span>
            {status.last_update_at && (
              <>
                {" · "}
                <span className="text-fg">
                  {new Date(status.last_update_at).toLocaleTimeString()}
                </span>
                {status.last_update_kind && ` (${status.last_update_kind})`}
              </>
            )}
          </span>
        )}
      </div>

      {status.last_error && (
        <div className="card border-bad/40 text-sm text-bad">
          Последняя ошибка: {status.last_error}
        </div>
      )}

      {status.bot && (status.update_count ?? 0) === 0 && (
        <div className="card text-sm text-muted">
          <p className="text-fg font-medium mb-1">
            Апдейтов от Telegram пока не приходило
          </p>
          <ul className="list-disc ml-5 mt-2 space-y-1">
            <li>
              webhook зарегистрирован при старте через start.py (Cloudflare
              tunnel)
            </li>
            <li>
              в Telegram → Settings → Business → Chatbots бот подключён, и в
              Manage messages выбраны нужные чаты
            </li>
            <li>backend доступен по HTTPS снаружи (ngrok/cloudflared)</li>
          </ul>
        </div>
      )}

      <div>
        <div className="flex items-center justify-between mb-3">
          <p className="section-label mb-0">Лента сообщений</p>
          {messages.length > 0 && (
            <span className="badge badge-zinc">{messages.length}</span>
          )}
        </div>

        {loaded && messages.length === 0 ? (
          <div className="card">
            <EmptyState
              icon={MessageSquare}
              title="Нет входящих сообщений"
              description="Они появятся когда бот получит первое сообщение в Telegram"
            />
          </div>
        ) : (
          <div className="grid gap-3">
            {messages.map((m) => (
              <MessageCard
                key={m.id}
                msg={m}
                onReply={!status.auto_reply ? setActive : undefined}
                onFeedback={status.auto_reply ? onFeedback : undefined}
              />
            ))}
          </div>
        )}
      </div>

      {active && (
        <ReplyModal
          message={active}
          onClose={() => setActive(null)}
          onSent={refresh}
        />
      )}

      {editingFeedback && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="card w-full max-w-xl animate-fadeIn">
            <p className="section-label">Исправь ответ</p>
            <p className="text-sm mb-3 text-muted whitespace-pre-wrap">
              На сообщение: {editingFeedback.text}
            </p>
            <textarea
              className="input min-h-[100px] resize-none"
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
