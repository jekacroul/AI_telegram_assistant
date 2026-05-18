import React, { useEffect, useState } from "react";
import { Zap, Plus, Trash2 } from "lucide-react";
import { api } from "../lib/api.js";
import { useLang } from "../hooks/useLang.js";
import Page from "../components/Page.jsx";
import Tile from "../components/Tile.jsx";

export default function QuickReplies() {
  const { t } = useLang();
  const [items, setItems] = useState([]);
  const [newText, setNewText] = useState("");

  async function load() {
    setItems(await api.quickReplies());
  }

  useEffect(() => {
    load();
  }, []);

  async function addReply(e) {
    e.preventDefault();
    if (!newText.trim()) return;
    await api.createQuickReply({ text: newText.trim(), category: "general" });
    setNewText("");
    load();
  }

  async function saveItem(id, text) {
    await api.updateQuickReply(id, { text });
    load();
  }

  async function removeItem(id) {
    await api.deleteQuickReply(id);
    load();
  }

  return (
    <Page>
      <Tile title={t("quick.title")} icon={Zap}>
        {items.length > 0 ? (
          <div className="grid gap-2">
            {items.map((it) => (
              <QuickRow
                key={it.id}
                item={it}
                onSave={saveItem}
                onDelete={removeItem}
              />
            ))}
          </div>
        ) : (
          <p className="text-xs text-zinc-400 dark:text-slate-500 py-4 text-center">
            {t("quick.placeholder")}
          </p>
        )}
        <form className="mt-4 flex gap-2" onSubmit={addReply}>
          <input
            className="input"
            value={newText}
            onChange={(e) => setNewText(e.target.value)}
            placeholder={t("quick.placeholder")}
          />
          <button className="btn-primary" type="submit">
            <Plus size={14} />
            {t("quick.add")}
          </button>
        </form>
      </Tile>
    </Page>
  );
}

function QuickRow({ item, onSave, onDelete }) {
  const { t } = useLang();
  const [text, setText] = useState(item.text);
  const dirty = text !== item.text;
  return (
    <div
      className="flex items-center gap-2 p-2 rounded-lg
                 bg-light-card2 dark:bg-dark-card2
                 border border-light-border dark:border-dark-border
                 hover:border-light-border2 dark:hover:border-dark-border2
                 transition-colors duration-100"
    >
      <span
        className="flex-shrink-0 min-w-7 h-6 px-1.5 rounded-md text-[11px]
                   font-semibold inline-flex items-center justify-center
                   bg-light-hover dark:bg-dark-hover
                   text-zinc-500 dark:text-slate-400"
        title={t("quick.add")}
      >
        {item.usage_count}
      </span>
      <input
        className="input"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <button
        className="btn-primary"
        disabled={!dirty}
        onClick={() => onSave(item.id, text)}
      >
        {t("common.save")}
      </button>
      <button
        className="btn-danger"
        onClick={() => onDelete(item.id)}
        title={t("common.delete")}
      >
        <Trash2 size={14} />
      </button>
    </div>
  );
}
