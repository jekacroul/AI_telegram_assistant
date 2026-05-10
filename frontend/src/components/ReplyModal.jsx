import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Button, Textarea } from "./Card.jsx";

export default function ReplyModal({ message, onClose, onSent }) {
  const [variants, setVariants] = useState([]);
  const [edited, setEdited] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [sending, setSending] = useState(null);

  useEffect(() => {
    let alive = true;
    async function fetchVariants() {
      setLoading(true);
      setError(null);
      try {
        const result = await api.generateReply({
          message_id: message.id,
          n_variants: 3,
        });
        if (!alive) return;
        setVariants(result.variants || []);
        setEdited(result.variants || []);
      } catch (e) {
        if (alive) setError(e.message);
      } finally {
        if (alive) setLoading(false);
      }
    }
    fetchVariants();
    return () => {
      alive = false;
    };
  }, [message]);

  async function regenerate() {
    setLoading(true);
    setError(null);
    try {
      const result = await api.generateReply({
        message_id: message.id,
        n_variants: 3,
        temperature: 0.95,
      });
      setVariants(result.variants || []);
      setEdited(result.variants || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function send(idx) {
    const text = edited[idx];
    if (!text || !text.trim()) return;
    setSending(idx);
    try {
      await api.sendReply({
        chat_id: message.chat_id,
        text,
        reply_to_message_id: message.message_id,
        incoming_message_id: message.id,
      });
      await api.approveReply({
        input_text: message.text,
        output_text: text,
        chat_id: message.chat_id,
      });
      onSent?.();
      onClose();
    } catch (e) {
      setError(e.message);
    } finally {
      setSending(null);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-2xl rounded-xl border border-slate-800 bg-slate-900 p-5">
        <header className="mb-3 flex items-start justify-between">
          <div>
            <p className="text-xs uppercase tracking-wide text-slate-500">
              {message.chat_name} · {message.sender_name}
            </p>
            <h3 className="mt-1 text-sm text-slate-200">{message.text}</h3>
          </div>
          <Button variant="ghost" onClick={onClose}>Close</Button>
        </header>

        {error && (
          <div className="mb-3 rounded border border-rose-700 bg-rose-900/40 px-3 py-2 text-sm text-rose-200">
            {error}
          </div>
        )}

        {loading && (
          <p className="text-sm text-slate-400">Generating variants…</p>
        )}

        {!loading && (
          <div className="space-y-3">
            {edited.map((variant, idx) => (
              <div key={idx} className="rounded border border-slate-800 bg-slate-950 p-3">
                <div className="mb-2 flex items-center justify-between text-xs text-slate-500">
                  <span>Variant {idx + 1}</span>
                </div>
                <Textarea
                  value={variant}
                  rows={3}
                  onChange={(e) => {
                    const next = [...edited];
                    next[idx] = e.target.value;
                    setEdited(next);
                  }}
                />
                <div className="mt-2 flex justify-end">
                  <Button
                    onClick={() => send(idx)}
                    disabled={sending !== null}
                    variant="success"
                  >
                    {sending === idx ? "Sending…" : "Send"}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}

        <footer className="mt-4 flex justify-between">
          <Button variant="ghost" onClick={regenerate} disabled={loading}>
            Regenerate
          </Button>
        </footer>
      </div>
    </div>
  );
}
