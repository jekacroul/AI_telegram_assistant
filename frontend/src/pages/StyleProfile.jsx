import React, { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api.js";
import { useLang } from "../hooks/useLang.js";

const FIELDS = [
  ["avg_message_length", "number"],
  ["uses_emoji", "bool"],
  ["emoji_frequency", "number"],
  ["uses_lowercase", "bool"],
  ["punctuation_style", "string"],
  ["tone", "string"],
  ["avg_response_delay_minutes", "number"],
];

function ProfileEditor({ profile, setProfile }) {
  const { t } = useLang();
  const updateField = (key, value) => setProfile((p) => ({ ...(p || {}), [key]: value }));
  return <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
    {FIELDS.map(([key, type]) => <div key={key} className="card">
      <div className="label">{t(`style.fields.${key}`)}</div>
      {type === "bool" ? (
        <label className="flex items-center gap-2 mt-2 text-sm">
          <input type="checkbox" checked={!!profile?.[key]} onChange={(e) => updateField(key, e.target.checked)} />
          {profile?.[key] ? t("common.yes") : t("common.no")}
        </label>
      ) : (
        <input
          className="input mt-1"
          type={type === "number" ? "number" : "text"}
          step="0.01"
          value={profile?.[key] ?? ""}
          onChange={(e) => updateField(key, type === "number" ? parseFloat(e.target.value) || 0 : e.target.value)}
        />
      )}
    </div>)}
  </div>;
}

export default function StyleProfile() {
  const { t } = useLang();
  const [settings, setSettings] = useState({ persona_mode: "global" });
  const [globalProfile, setGlobalProfile] = useState({});
  const [personas, setPersonas] = useState([]);
  const [chats, setChats] = useState([]);
  const [selected, setSelected] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const refresh = async () => {
    setError("");
    const [s, gp, ps, cs] = await Promise.all([
      api.getSettings(),
      api.styleProfile(),
      api.personas(),
      api.chats(),
    ]);
    setSettings(s);
    setGlobalProfile(gp || {});
    setPersonas(ps || []);
    setChats(cs || []);
  };

  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, []);

  const saveMode = async (mode) => {
    setSaving(true);
    try {
      await api.saveSettings({ persona_mode: mode });
      setSettings((p) => ({ ...p, persona_mode: mode }));
    } finally {
      setSaving(false);
    }
  };

  const chatCards = useMemo(() => {
    const byId = new Map((personas || []).map((p) => [p.chat_id, p]));
    return (chats || []).map((c) => {
      const persona = byId.get(c.chat_id);
      return {
        chat_id: c.chat_id,
        chat_name: c.chat_name,
        updated_at: persona?.updated_at || null,
        profile: persona?.profile || null,
      };
    });
  }, [personas, chats]);

  return <div className="space-y-4">
    <div className="card flex gap-2 items-center">
      <button className={`btn-secondary ${settings.persona_mode === "global" ? "ring-2 ring-accent" : ""}`} onClick={() => saveMode("global")} disabled={saving}>{t("style.globalStyle")}</button>
      <button className={`btn-secondary ${settings.persona_mode === "per_chat" ? "ring-2 ring-accent" : ""}`} onClick={() => saveMode("per_chat")} disabled={saving}>{t("style.perChatStyle")}</button>
      {error && <span className="text-bad text-sm">{error}</span>}
    </div>

    {settings.persona_mode === "global" && <>
      <div className="flex gap-2">
        <button className="btn-primary" onClick={async () => { setSaving(true); try { await api.saveStyle(globalProfile || {}); } finally { setSaving(false); } }} disabled={saving}>{t("common.save")}</button>
        <button className="btn-secondary" onClick={async () => { setSaving(true); try { setGlobalProfile(await api.reanalyzeStyle()); } finally { setSaving(false); } }} disabled={saving}>{t("style.recalculate")}</button>
      </div>
      <ProfileEditor profile={globalProfile} setProfile={setGlobalProfile} />
    </>}

    {settings.persona_mode === "per_chat" && <>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {chatCards.map((p) => <div key={p.chat_id} className="card cursor-pointer" onClick={() => setSelected({ ...p, profile: p.profile || { ...globalProfile } })}>
          <div className="font-medium">{p.chat_name || p.chat_id}</div>
          <div className="text-sm text-muted">{t("style.tone", { value: p.profile?.tone || t("common.dash") })}</div>
          <div className="text-sm text-muted">{t("style.avgLength", { value: p.profile?.avg_message_length || 0 })}</div>
          <div className="text-xs text-muted">{t("style.updated", { value: p.updated_at || t("common.none") })}</div>
        </div>)}
      </div>
      {!chatCards.length && <div className="card text-muted text-sm">{t("style.noChats")}</div>}
      {selected && <div className="space-y-3">
        <div className="card font-medium">{t("style.editing", { name: selected.chat_name || selected.chat_id })}</div>
        <ProfileEditor profile={selected.profile || {}} setProfile={(updater) => setSelected((prev) => ({ ...prev, profile: typeof updater === "function" ? updater(prev.profile || {}) : updater }))} />
        <div className="flex gap-2">
          <button className="btn-primary" disabled={saving} onClick={async () => { setSaving(true); try { await api.savePersona(selected.chat_id, selected.profile || {}); await refresh(); } finally { setSaving(false); } }}>{t("common.save")}</button>
          <button className="btn-secondary" disabled={saving} onClick={async () => { setSaving(true); try { const p = await api.reanalyzePersona(selected.chat_id); setSelected((s) => ({ ...s, profile: p })); await refresh(); } finally { setSaving(false); } }}>{t("style.recalculate")}</button>
          <button className="btn-secondary" disabled={saving} onClick={async () => { setSaving(true); try { await api.deletePersona(selected.chat_id); setSelected(null); await refresh(); } finally { setSaving(false); } }}>{t("common.delete")}</button>
        </div>
      </div>}
    </>}
  </div>;
}
