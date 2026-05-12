const BASE = "";

async function request(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let err;
    try {
      err = await res.json();
    } catch {
      err = { detail: res.statusText };
    }
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  status: () => request("/api/status"),
  chats: () => request("/api/chats"),
  getSettings: () => request("/api/settings"),
  saveSettings: (data) =>
    request("/api/settings", { method: "POST", body: JSON.stringify(data) }),
  pending: () => request("/api/messages/pending"),
  recent: (limit = 100) => request(`/api/messages/recent?limit=${limit}`),
  generateReply: (message_id) =>
    request("/api/reply/generate", {
      method: "POST",
      body: JSON.stringify({ message_id }),
    }),
  approveReply: (message_id, text) =>
    request("/api/reply/approve", {
      method: "POST",
      body: JSON.stringify({ message_id, text }),
    }),
  sendReply: (chat_id, text, reply_to, original_id) =>
    request("/api/reply/send", {
      method: "POST",
      body: JSON.stringify({ chat_id, text, reply_to, original_id }),
    }),
  feedback: (message_id, feedback, corrected_text) =>
    request("/api/reply/feedback", {
      method: "POST",
      body: JSON.stringify({ message_id, feedback, corrected_text }),
    }),
  trainingStatus: () => request("/api/training/status"),
  buildDataset: () => request("/api/training/build-dataset", { method: "POST" }),
  startTraining: () => request("/api/training/start", { method: "POST" }),
  cancelTraining: () => request("/api/training/cancel", { method: "POST" }),
  activateAdapter: (run_id) =>
    request(`/api/training/activate/${run_id}`, { method: "POST" }),
  trainingRuns: () => request("/api/training/runs"),
  styleProfile: () => request("/api/style/profile"),
  saveStyle: (profile) =>
    request("/api/style/profile", {
      method: "PUT",
      body: JSON.stringify(profile),
    }),
  reanalyzeStyle: () =>
    request("/api/style/reanalyze", { method: "POST" }),
  testLLM: (text) =>
    request("/api/llm/test", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  listModels: () => request("/api/llm/models"),
  dialogsChats: () => request("/api/dialogs/chats"),
  dialogsVersions: (chat_id) => request(`/api/dialogs/${chat_id}/versions`),
  dialogsBackup: (backup_id) => request(`/api/dialogs/backup/${backup_id}`),
  dialogsSettings: () => request("/api/dialogs/settings"),
  saveDialogsSettings: (data) =>
    request("/api/dialogs/settings", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  dialogsRunBackup: () =>
    request("/api/dialogs/run-backup", { method: "POST" }),
  getNotifyChat: () => request("/api/settings/notify-chat"),
  saveNotifyChat: (data) =>
    request("/api/settings/notify-chat", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  detectNotifyChat: () => request("/api/settings/notify-chat/detect"),
  statsOverview: () => request("/api/stats/overview"),
  statsActivity: () => request("/api/stats/activity"),
  statsTopChats: () => request("/api/stats/top-chats"),
  statsModelQuality: () => request("/api/stats/model-quality"),
  statsResponseTime: () => request("/api/stats/response-time"),
};

export function streamEvents(path, onEvent) {
  const ev = new EventSource(path);
  ev.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data));
    } catch {}
  };
  return () => ev.close();
}
