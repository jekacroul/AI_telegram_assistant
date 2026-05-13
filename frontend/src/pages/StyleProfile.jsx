import React, { useEffect, useState } from "react";
import { api } from "../lib/api.js";

export default function StyleProfile() {
  const [settings, setSettings] = useState({ persona_mode: "global" });
  const [profile, setProfile] = useState(null);
  const [personas, setPersonas] = useState([]);
  const [selected, setSelected] = useState(null);

  const refresh = async () => {
    const s = await api.getSettings();
    setSettings(s);
    setProfile(await api.styleProfile());
    setPersonas(await api.personas());
  };
  useEffect(() => { refresh(); }, []);

  const saveMode = async (mode) => {
    await api.saveSettings({ persona_mode: mode });
    setSettings((p) => ({ ...p, persona_mode: mode }));
  };

  return <div className="space-y-4">
    <div className="card flex gap-2 items-center">
      <button className={`btn-secondary ${settings.persona_mode === "global" ? "ring-2 ring-accent" : ""}`} onClick={() => saveMode("global")}>Глобальный стиль</button>
      <button className={`btn-secondary ${settings.persona_mode === "per_chat" ? "ring-2 ring-accent" : ""}`} onClick={() => saveMode("per_chat")}>Стиль по чатам</button>
    </div>

    {settings.persona_mode === "global" && <div className="card text-sm text-muted">Глобальный профиль активен.</div>}

    {settings.persona_mode === "per_chat" && <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {personas.map((p) => <div key={p.chat_id} className="card cursor-pointer" onClick={() => setSelected(p)}>
        <div className="font-medium">{p.chat_name || p.chat_id}</div>
        <div className="text-sm text-muted">Тон: {p.profile?.tone || "-"}</div>
        <div className="text-sm text-muted">Средняя длина: {p.profile?.avg_message_length || 0}</div>
        <div className="text-xs text-muted">Обновлено: {p.updated_at || "-"}</div>
      </div>)}
    </div>}

    {selected && <div className="card space-y-2">
      <div className="font-medium">Редактирование: {selected.chat_name || selected.chat_id}</div>
      <input className="input" value={selected.profile?.tone || ""} onChange={(e) => setSelected((s) => ({ ...s, profile: { ...(s.profile || {}), tone: e.target.value } }))} />
      <input className="input" type="number" value={selected.profile?.avg_message_length || 0} onChange={(e) => setSelected((s) => ({ ...s, profile: { ...(s.profile || {}), avg_message_length: parseFloat(e.target.value) || 0 } }))} />
      <div className="flex gap-2">
        <button className="btn-primary" onClick={async () => { await api.savePersona(selected.chat_id, selected.profile || {}); await refresh(); }}>Сохранить</button>
        <button className="btn-secondary" onClick={async () => { const profile2 = await api.reanalyzePersona(selected.chat_id); setSelected((s) => ({ ...s, profile: profile2 })); await refresh(); }}>Пересчитать из сообщений</button>
        <button className="btn-secondary" onClick={async () => { await api.deletePersona(selected.chat_id); setSelected(null); await refresh(); }}>Удалить</button>
      </div>
    </div>}
  </div>;
}
