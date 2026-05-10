const BASE = "/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const txt = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${txt}`);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res.text();
}

export const api = {
  health: () => request("/health"),
  llmStatus: () => request("/llm/status"),
  llmTest: (prompt) =>
    request("/llm/test", { method: "POST", body: JSON.stringify({ prompt }) }),
  pullModel: () => request("/llm/pull", { method: "POST" }),

  chats: () => request("/chats"),
  monitoredChats: () => request("/chats/monitored"),
  selectChats: (chatIds, names) =>
    request("/chats/select", {
      method: "POST",
      body: JSON.stringify({ chat_ids: chatIds, names }),
    }),

  pendingMessages: () => request("/messages/pending"),
  generateReply: (payload) =>
    request("/reply/generate", { method: "POST", body: JSON.stringify(payload) }),
  sendReply: (payload) =>
    request("/reply/send", { method: "POST", body: JSON.stringify(payload) }),
  approveReply: (payload) =>
    request("/reply/approve", { method: "POST", body: JSON.stringify(payload) }),

  trainingStatus: () => request("/training/status"),
  trainingDatasetBuild: () => request("/training/dataset/build", { method: "POST" }),
  trainingDatasetExport: () => request("/training/dataset/export", { method: "POST" }),
  trainingStart: (payload) =>
    request("/training/start", {
      method: "POST",
      body: JSON.stringify(payload || {}),
    }),
  trainingCancel: () => request("/training/cancel", { method: "POST" }),
  trainingActivate: (version) =>
    request(`/training/activate/${version}`, { method: "POST" }),

  styleProfile: () => request("/style/profile"),
  styleReanalyze: () => request("/style/reanalyze", { method: "POST" }),
  styleManual: (profile) =>
    request("/style/manual", { method: "POST", body: JSON.stringify(profile) }),

  settings: () => request("/settings"),
  saveSettings: (payload) =>
    request("/settings", { method: "POST", body: JSON.stringify(payload) }),
  savePersona: (persona) =>
    request("/settings/persona", { method: "POST", body: JSON.stringify({ persona }) }),
};

export function streamEvents(onEvent) {
  const es = new EventSource(`${BASE}/stream/events`);
  es.onmessage = (ev) => {
    try {
      onEvent(JSON.parse(ev.data));
    } catch (_) {
      /* ignore */
    }
  };
  ["incoming_message"].forEach((name) => {
    es.addEventListener(name, (ev) => {
      try {
        onEvent({ type: name, ...JSON.parse(ev.data) });
      } catch (_) {
        /* ignore */
      }
    });
  });
  return es;
}

export function streamTrainingProgress(onProgress) {
  const es = new EventSource(`${BASE}/training/progress`);
  es.addEventListener("progress", (ev) => {
    try {
      onProgress(JSON.parse(ev.data));
    } catch (_) {
      /* ignore */
    }
  });
  return es;
}
