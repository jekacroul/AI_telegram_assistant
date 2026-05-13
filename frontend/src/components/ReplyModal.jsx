import React, { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { useNavigate } from "react-router-dom";

export default function ReplyModal({ message, onClose, onSent }) {
  const [variants, setVariants] = useState([]);
  const [loading, setLoading] = useState(true);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [quickReplies, setQuickReplies] = useState([]);
  const [newQuickText, setNewQuickText] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    api
      .generateReply(message.id)
      .then((res) => {
        if (cancelled) return;
        setVariants(res.variants || []);
        setText(res.variants?.[0] || "");
      })
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [message.id]);

  useEffect(() => {
    api.quickReplies().then((rows) => setQuickReplies(rows.slice(0, 5))).catch(() => {});
  }, []);

  async function send() {
    setSending(true);
    setError("");
    try {
      await api.approveReply(message.id, text);
      onSent?.();
      onClose();
    } catch (e) {
      setError(e.message);
    } finally {
      setSending(false);
    }
  }

  async function addQuickReply() {
    if (!newQuickText.trim()) return;
    await api.createQuickReply({ text: newQuickText.trim(), category: "general" });
    setNewQuickText("");
    const rows = await api.quickReplies();
    setQuickReplies(rows.slice(0, 5));
  }

  async function applyQuickReply(reply) {
    setText(reply.text);
    await api.useQuickReply(reply.id);
  }

  return (
    <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4">
      <div className="card w-full max-w-2xl">
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="label">Сообщение от {message.sender_name}</div>
            <div className="text-sm mt-1 whitespace-pre-wrap">{message.text}</div>
          </div>
          <button className="btn-secondary" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="label mt-4">Варианты ответа</div>
        {loading && <div className="text-sm text-muted">Генерирую...</div>}
        {!loading && (
          <div className="grid gap-2 mt-2">
            {variants.map((v, i) => (
              <button
                key={i}
                className={`text-left p-3 rounded-md border text-sm ${
                  text === v
                    ? "border-accent bg-accent/10"
                    : "border-white/10 hover:border-white/20"
                }`}
                onClick={() => setText(v)}
              >
                {v}
              </button>
            ))}
          </div>
        )}

        <div className="label mt-4">Текст ответа (можно отредактировать)</div>
        <div className="mt-2">
          <div className="label">Быстрые ответы</div>
          <div className="flex flex-wrap gap-2 mt-2">
            {quickReplies.map((q) => (
              <button key={q.id} className="btn-secondary text-xs" onClick={() => applyQuickReply(q)}>
                {q.text}
              </button>
            ))}
            <input
              className="input max-w-[220px]"
              value={newQuickText}
              onChange={(e) => setNewQuickText(e.target.value)}
              placeholder="+ новый"
            />
            <button className="btn-secondary" onClick={addQuickReply}>+</button>
            <button className="btn-secondary" onClick={() => navigate("/quick-replies")}>Управление</button>
          </div>
        </div>
        <textarea
          className="input mt-1 min-h-[100px]"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />

        {error && <div className="text-bad text-sm mt-2">{error}</div>}

        <div className="flex justify-end gap-2 mt-4">
          <button className="btn-secondary" onClick={onClose}>
            Отмена
          </button>
          <button
            className="btn-primary"
            onClick={send}
            disabled={sending || !text.trim()}
          >
            {sending ? "Отправляю..." : "Отправить"}
          </button>
        </div>
      </div>
    </div>
  );
}
