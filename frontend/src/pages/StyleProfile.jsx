import { useEffect, useState } from "react";
import { Card, Button, Textarea } from "../components/Card.jsx";
import { api } from "../api.js";

export default function StyleProfile() {
  const [profile, setProfile] = useState(null);
  const [samples, setSamples] = useState([]);
  const [editing, setEditing] = useState(false);
  const [editJson, setEditJson] = useState("");
  const [error, setError] = useState(null);

  async function load() {
    try {
      const data = await api.styleProfile();
      setProfile(data.profile);
      setSamples(data.samples || []);
      setEditJson(JSON.stringify(data.profile || {}, null, 2));
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function reanalyze() {
    setError(null);
    try {
      const data = await api.styleReanalyze();
      setProfile(data.profile);
      setEditJson(JSON.stringify(data.profile || {}, null, 2));
    } catch (e) {
      setError(e.message);
    }
  }

  async function saveManual() {
    setError(null);
    try {
      const parsed = JSON.parse(editJson);
      await api.styleManual(parsed);
      setEditing(false);
      load();
    } catch (e) {
      setError(`Invalid JSON: ${e.message}`);
    }
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded border border-rose-700 bg-rose-900/40 px-3 py-2 text-sm text-rose-200">
          {error}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-3">
        <Card title="Avg length (words)">
          <p className="text-2xl font-semibold text-white">
            {profile?.avg_length_words ?? "—"}
          </p>
        </Card>
        <Card title="Emoji %">
          <p className="text-2xl font-semibold text-white">
            {profile?.emoji_percentage ?? "—"}%
          </p>
        </Card>
        <Card title="Messages analysed">
          <p className="text-2xl font-semibold text-white">
            {profile?.messages_count ?? "—"}
          </p>
        </Card>
      </div>

      <Card
        title="Common phrases"
        action={<Button variant="ghost" onClick={reanalyze}>Reanalyze</Button>}
      >
        {!profile?.common_phrases?.length ? (
          <p className="text-sm text-slate-400">No data yet.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {profile.common_phrases.map((p) => (
              <span
                key={p.phrase}
                className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-200"
              >
                {p.phrase} <span className="text-slate-500">×{p.count}</span>
              </span>
            ))}
          </div>
        )}
      </Card>

      <Card title="Common words">
        {!profile?.common_words?.length ? (
          <p className="text-sm text-slate-400">No data yet.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {profile.common_words.map((w) => (
              <span
                key={w.word}
                className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-200"
              >
                {w.word} <span className="text-slate-500">×{w.count}</span>
              </span>
            ))}
          </div>
        )}
      </Card>

      <Card
        title="Manual edit (JSON)"
        action={
          editing ? (
            <div className="flex gap-2">
              <Button variant="success" onClick={saveManual}>Save</Button>
              <Button variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
            </div>
          ) : (
            <Button variant="ghost" onClick={() => setEditing(true)}>Edit</Button>
          )
        }
      >
        {editing ? (
          <Textarea
            value={editJson}
            rows={14}
            onChange={(e) => setEditJson(e.target.value)}
          />
        ) : (
          <pre className="max-h-72 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-300">
            {JSON.stringify(profile, null, 2)}
          </pre>
        )}
      </Card>

      <Card title="Sample messages from DB">
        {samples.length === 0 ? (
          <p className="text-sm text-slate-400">No messages collected yet.</p>
        ) : (
          <ul className="space-y-2">
            {samples.map((s) => (
              <li key={s.id} className="rounded border border-slate-800 bg-slate-950 p-3">
                <div className="flex justify-between text-xs text-slate-500">
                  <span>{s.chat_name}</span>
                  <span>{new Date(s.timestamp).toLocaleString()}</span>
                </div>
                <p className="mt-1 text-sm text-slate-200">{s.text}</p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
