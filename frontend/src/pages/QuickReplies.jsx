import React, { useEffect, useState } from "react";
import { api } from "../lib/api.js";

export default function QuickReplies() {
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
    <div className="space-y-4">
      <div className="card">
        <div className="label">Быстрые ответы</div>
        <div className="grid gap-2 mt-3">
          {items.map((it) => (
            <QuickRow key={it.id} item={it} onSave={saveItem} onDelete={removeItem} />
          ))}
        </div>
        <form className="mt-4 flex gap-2" onSubmit={addReply}>
          <input
            className="input"
            value={newText}
            onChange={(e) => setNewText(e.target.value)}
            placeholder="Новый быстрый ответ"
          />
          <button className="btn-primary" type="submit">Добавить</button>
        </form>
      </div>
    </div>
  );
}

function QuickRow({ item, onSave, onDelete }) {
  const [text, setText] = useState(item.text);
  return (
    <div className="flex items-center gap-2 border border-line rounded-md p-2">
      <span className="text-xs px-2 py-1 rounded-full bg-surface">{item.usage_count}</span>
      <input className="input" value={text} onChange={(e) => setText(e.target.value)} />
      <button className="btn-secondary" onClick={() => onSave(item.id, text)}>Сохранить</button>
      <button className="btn-secondary text-bad" onClick={() => onDelete(item.id)}>Удалить</button>
    </div>
  );
}
