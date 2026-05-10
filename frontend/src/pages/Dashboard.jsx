import { useEffect, useState } from "react";
import { Card, Button } from "../components/Card.jsx";
import ReplyModal from "../components/ReplyModal.jsx";
import { api, streamEvents } from "../api.js";

export default function Dashboard() {
  const [messages, setMessages] = useState([]);
  const [archive, setArchive] = useState({});
  const [active, setActive] = useState(null);
  const [llm, setLlm] = useState(null);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    try {
      const [m, s] = await Promise.all([api.pendingMessages(), api.llmStatus()]);
      setMessages(m);
      setLlm(s);
    } catch (_) {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    const es = streamEvents((evt) => {
      if (evt.type === "incoming_message") {
        setMessages((prev) => {
          if (prev.find((m) => m.id === evt.id)) return prev;
          return [evt, ...prev];
        });
      }
    });
    const id = setInterval(refresh, 15000);
    return () => {
      es.close();
      clearInterval(id);
    };
  }, []);

  function onSent() {
    if (active) {
      setArchive((prev) => ({ ...prev, [active.id]: true }));
      setMessages((prev) => prev.filter((m) => m.id !== active.id));
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-3">
        <Card title="Ollama">
          <p className="text-sm">
            <span
              className={`mr-2 inline-block h-2 w-2 rounded-full ${
                llm?.running ? "bg-emerald-400" : "bg-rose-500"
              }`}
            />
            {llm?.running ? `Running · ${llm.model}` : "Offline"}
          </p>
          {llm?.active_adapter && (
            <p className="mt-1 text-xs text-slate-400">
              Adapter: {llm.active_adapter}
            </p>
          )}
        </Card>
        <Card title="Pending replies">
          <p className="text-2xl font-semibold text-white">{messages.length}</p>
          <p className="text-xs text-slate-400">unhandled incoming messages</p>
        </Card>
        <Card title="Archived this session">
          <p className="text-2xl font-semibold text-white">
            {Object.keys(archive).length}
          </p>
          <p className="text-xs text-slate-400">replies sent</p>
        </Card>
      </div>

      <Card
        title="Incoming messages"
        action={
          <Button variant="ghost" onClick={refresh} disabled={loading}>
            {loading ? "…" : "Refresh"}
          </Button>
        }
      >
        {messages.length === 0 ? (
          <p className="text-sm text-slate-400">No pending messages.</p>
        ) : (
          <ul className="divide-y divide-slate-800">
            {messages.map((m) => (
              <li
                key={m.id}
                className={`flex cursor-pointer items-start gap-3 py-3 px-1 transition hover:bg-slate-800/50 ${
                  archive[m.id] ? "opacity-60" : ""
                }`}
                onClick={() => setActive(m)}
              >
                <div
                  className={`mt-1 h-2.5 w-2.5 rounded-full ${
                    archive[m.id] ? "bg-emerald-400" : "bg-amber-400"
                  }`}
                />
                <div className="flex-1">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-slate-200">
                      {m.sender_name}
                    </span>
                    <span className="text-xs text-slate-500">
                      {new Date(m.timestamp).toLocaleString()}
                    </span>
                  </div>
                  <p className="mt-0.5 text-xs text-slate-500">{m.chat_name}</p>
                  <p className="mt-1 text-sm text-slate-300 line-clamp-3">{m.text}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {active && (
        <ReplyModal
          message={active}
          onClose={() => setActive(null)}
          onSent={onSent}
        />
      )}
    </div>
  );
}
