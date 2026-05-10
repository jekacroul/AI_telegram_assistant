import { useEffect, useState } from "react";
import { Card, Button, Input, Textarea } from "../components/Card.jsx";
import { api } from "../api.js";

const MODEL_OPTIONS = [
  { value: "mistral:7b", label: "mistral:7b (best for Russian, ~6GB VRAM)" },
  { value: "llama3.1:8b", label: "llama3.1:8b (~6GB VRAM)" },
  { value: "llama3.2:3b", label: "llama3.2:3b (low RAM)" },
];

export default function Settings() {
  const [form, setForm] = useState({
    telegram_api_id: "",
    telegram_api_hash: "",
    telegram_phone: "",
    ollama_model: "mistral:7b",
    ollama_host: "http://127.0.0.1:11434",
    persona: "",
  });
  const [chats, setChats] = useState([]);
  const [monitored, setMonitored] = useState({});
  const [test, setTest] = useState({ prompt: "Привет!", response: "" });
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [info, setInfo] = useState(null);

  async function load() {
    try {
      const cur = await api.settings();
      setForm((f) => ({ ...f, ...cur }));
    } catch (_) {
      /* ignore */
    }
    try {
      const c = await api.chats();
      setChats(c);
      const m = {};
      c.forEach((row) => (m[row.chat_id] = row.monitored));
      setMonitored(m);
    } catch (_) {
      /* ignore */
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function save() {
    setError(null);
    setInfo(null);
    setSaving(true);
    try {
      await api.saveSettings({
        telegram_api_id: form.telegram_api_id,
        telegram_api_hash: form.telegram_api_hash,
        telegram_phone: form.telegram_phone,
        ollama_model: form.ollama_model,
        ollama_host: form.ollama_host,
      });
      await api.savePersona(form.persona);
      setInfo("Saved.");
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function applyChats() {
    setError(null);
    const ids = Object.entries(monitored)
      .filter(([, v]) => v)
      .map(([k]) => Number(k));
    const names = {};
    chats.forEach((c) => (names[c.chat_id] = c.name));
    try {
      const res = await api.selectChats(ids, names);
      setInfo(`Imported ${res.messages_imported} messages.`);
    } catch (e) {
      setError(e.message);
    }
  }

  async function runTest() {
    setError(null);
    try {
      const r = await api.llmTest(test.prompt);
      setTest({ ...test, response: r.response });
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded border border-rose-700 bg-rose-900/40 px-3 py-2 text-sm text-rose-200">
          {error}
        </div>
      )}
      {info && (
        <div className="rounded border border-emerald-700 bg-emerald-900/40 px-3 py-2 text-sm text-emerald-200">
          {info}
        </div>
      )}

      <Card title="Telegram credentials">
        <div className="grid gap-3 md:grid-cols-2">
          <label className="text-sm text-slate-300">
            API ID
            <Input
              value={form.telegram_api_id}
              onChange={(e) => setForm({ ...form, telegram_api_id: e.target.value })}
            />
          </label>
          <label className="text-sm text-slate-300">
            API Hash
            <Input
              value={form.telegram_api_hash}
              onChange={(e) => setForm({ ...form, telegram_api_hash: e.target.value })}
            />
          </label>
          <label className="text-sm text-slate-300">
            Phone (E.164)
            <Input
              value={form.telegram_phone}
              onChange={(e) => setForm({ ...form, telegram_phone: e.target.value })}
            />
          </label>
        </div>
        <p className="mt-2 text-xs text-slate-400">
          After saving credentials, run{" "}
          <code className="rounded bg-slate-800 px-1">python -m backend.telegram_login</code>{" "}
          once in your terminal to authorize the session.
        </p>
      </Card>

      <Card title="Ollama">
        <div className="grid gap-3 md:grid-cols-2">
          <label className="text-sm text-slate-300">
            Host
            <Input
              value={form.ollama_host}
              onChange={(e) => setForm({ ...form, ollama_host: e.target.value })}
            />
          </label>
          <label className="text-sm text-slate-300">
            Model
            <select
              className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-100"
              value={form.ollama_model}
              onChange={(e) => setForm({ ...form, ollama_model: e.target.value })}
            >
              {MODEL_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="mt-3 flex gap-2">
          <Button onClick={() => api.pullModel().then(() => setInfo("Pulled."))}>
            Pull model
          </Button>
        </div>
      </Card>

      <Card title="Persona / extra style notes">
        <Textarea
          rows={4}
          value={form.persona}
          onChange={(e) => setForm({ ...form, persona: e.target.value })}
          placeholder="e.g. Talk casually, prefer short replies, no emoji on Mondays."
        />
      </Card>

      <Card title="Monitored chats" action={<Button variant="ghost" onClick={applyChats}>Apply & import</Button>}>
        {chats.length === 0 ? (
          <p className="text-sm text-slate-400">
            Telegram is not connected yet. Save credentials, run the login script, then refresh.
          </p>
        ) : (
          <div className="max-h-72 space-y-1 overflow-auto">
            {chats.map((c) => (
              <label
                key={c.chat_id}
                className="flex items-center gap-3 rounded px-2 py-1 hover:bg-slate-800/40"
              >
                <input
                  type="checkbox"
                  checked={!!monitored[c.chat_id]}
                  onChange={(e) =>
                    setMonitored({ ...monitored, [c.chat_id]: e.target.checked })
                  }
                />
                <span className="flex-1 text-sm text-slate-200">{c.name}</span>
                <span className="text-xs text-slate-500">{c.type}</span>
              </label>
            ))}
          </div>
        )}
      </Card>

      <Card title="Test model">
        <Textarea
          rows={2}
          value={test.prompt}
          onChange={(e) => setTest({ ...test, prompt: e.target.value })}
        />
        <div className="mt-2 flex justify-end">
          <Button onClick={runTest}>Send test</Button>
        </div>
        {test.response && (
          <pre className="mt-3 max-h-60 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-200">
            {test.response}
          </pre>
        )}
      </Card>

      <div className="flex justify-end">
        <Button onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save settings"}
        </Button>
      </div>
    </div>
  );
}
