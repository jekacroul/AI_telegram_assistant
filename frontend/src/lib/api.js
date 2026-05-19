const BASE = "";

export function apiUrl(path) {
  return BASE + path;
}

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
  systemResources: () => request("/api/system/resources"),
  chats: () => request("/api/chats"),
  getSettings: () => request("/api/settings"),
  getSchedule: () => request("/api/schedule"),
  getDelay: () => request("/api/delay"),
  saveSchedule: (data) =>
    request("/api/schedule", { method: "POST", body: JSON.stringify(data) }),
  saveDelay: (data) =>
    request("/api/delay", { method: "POST", body: JSON.stringify(data) }),
  saveSettings: (data) =>
    request("/api/settings", { method: "POST", body: JSON.stringify(data) }),
  pending: () => request("/api/messages/pending"),
  reconcileQueue: () =>
    request("/api/messages/reconcile-queue", { method: "POST" }),
  clearQueue: () =>
    request("/api/messages/clear-queue", { method: "POST" }),
  recent: (limit = 100) => request(`/api/messages/recent?limit=${limit}`),
  generateReply: (message_id, transcription_override) =>
    request("/api/reply/generate", {
      method: "POST",
      body: JSON.stringify({
        message_id,
        ...(transcription_override !== undefined && transcription_override !== null
          ? { transcription_override }
          : {}),
      }),
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
  qualityStats: () => request("/api/quality/stats"),
  buildDataset: (config) =>
    request("/api/training/build-dataset", {
      method: "POST",
      ...(config ? { body: JSON.stringify(config) } : {}),
    }),
  validateExport: (file_path) =>
    request("/api/training/export/validate", {
      method: "POST",
      body: JSON.stringify({ file_path }),
    }),
  parseExport: (file_path) =>
    request("/api/training/export/parse", {
      method: "POST",
      body: JSON.stringify({ file_path }),
    }),
  cachedExports: () => request("/api/training/exports/cached"),
  startTraining: (source) =>
    request("/api/training/start", {
      method: "POST",
      ...(source ? { body: JSON.stringify({ source }) } : {}),
    }),
  cancelTraining: () => request("/api/training/cancel", { method: "POST" }),
  activateAdapter: (run_id) =>
    request(`/api/training/activate/${run_id}`, { method: "POST" }),
  deactivateAdapter: () =>
    request("/api/training/deactivate", { method: "POST" }),
  deleteTrainingRun: (run_id) =>
    request(`/api/training/runs/${run_id}`, { method: "DELETE" }),
  trainingRunErrorLog: (run_id) =>
    request(`/api/training/runs/${run_id}/error-log`),
  exportGguf: (run_id) =>
    request(`/api/training/runs/${run_id}/export-gguf`, { method: "POST" }),
  exportLoraGguf: (run_id) =>
    request(`/api/training/runs/${run_id}/export-lora-gguf`, { method: "POST" }),
  llamaServerStatus: () => request("/api/llama-server/status"),
  llamaServerStart: () => request("/api/llama-server/start", { method: "POST" }),
  llamaServerStop: () => request("/api/llama-server/stop", { method: "POST" }),
  llamaServerRestart: () =>
    request("/api/llama-server/restart", { method: "POST" }),
  llamaServerSetAutoResume: (enabled) =>
    request("/api/llama-server/auto-resume", {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  trainingRuns: () => request("/api/training/runs"),
  styleProfile: () => request("/api/style/profile"),
  saveStyle: (profile) =>
    request("/api/style/profile", {
      method: "PUT",
      body: JSON.stringify(profile),
    }),
  reanalyzeStyle: () =>
    request("/api/style/reanalyze", { method: "POST" }),
  personas: () => request("/api/personas"),
  persona: (chatId) => request(`/api/personas/${chatId}`),
  savePersona: (chatId, profile) =>
    request(`/api/personas/${chatId}`, { method: "PUT", body: JSON.stringify(profile) }),
  reanalyzePersona: (chatId) =>
    request(`/api/personas/${chatId}/reanalyze`, { method: "POST" }),
  deletePersona: (chatId) => request(`/api/personas/${chatId}`, { method: "DELETE" }),
  testLLM: (text) =>
    request("/api/llm/test", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  listModels: () => request("/api/llm/models"),
  dialogsChats: () => request("/api/dialogs/chats"),
  dialogsVersions: (chat_id) => request(`/api/dialogs/${chat_id}/versions`),
  dialogsBackup: (backup_id) => request(`/api/dialogs/backup/${backup_id}`),
  dialogsBackupExportUrl: (backup_id) =>
    apiUrl(`/api/dialogs/backup/${backup_id}/export`),
  deleteDialogsBackup: (backup_id) =>
    request(`/api/dialogs/backup/${backup_id}`, { method: "DELETE" }),
  deleteDialogsHistory: (chat_id) =>
    request(`/api/dialogs/${chat_id}/history`, { method: "DELETE" }),
  dialogsSettings: () => request("/api/dialogs/settings"),
  saveDialogsSettings: (data) =>
    request("/api/dialogs/settings", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  dialogsRunBackup: () =>
    request("/api/dialogs/run-backup", { method: "POST" }),
  getAdminSettings: () => request("/api/admin/settings"),
  saveAdminSettings: (data) =>
    request("/api/admin/settings", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  detectOwner: () => request("/api/settings/detect-owner"),
  adminNotifyTest: () => request("/api/admin/notify-test", { method: "POST" }),
  statsOverview: () => request("/api/stats/overview"),
  statsActivity: () => request("/api/stats/activity"),
  statsTopChats: () => request("/api/stats/top-chats"),
  statsModelQuality: () => request("/api/stats/model-quality"),
  statsResponseTime: () => request("/api/stats/response-time"),
  quickReplies: () => request("/api/quick-replies"),
  createQuickReply: (data) =>
    request("/api/quick-replies", { method: "POST", body: JSON.stringify(data) }),
  updateQuickReply: (id, data) =>
    request(`/api/quick-replies/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  deleteQuickReply: (id) =>
    request(`/api/quick-replies/${id}`, { method: "DELETE" }),
  useQuickReply: (id) =>
    request(`/api/quick-replies/${id}/use`, { method: "POST" }),
  whisperStatus: () => request("/api/whisper/status"),
  whisperUnload: () => request("/api/whisper/unload", { method: "POST" }),
  whisperTranscribe: (message_id) =>
    request("/api/whisper/transcribe", {
      method: "POST",
      body: JSON.stringify({ message_id }),
    }),
  whisperRetranscribe: (message_id) =>
    request("/api/whisper/retranscribe", {
      method: "POST",
      body: JSON.stringify({ message_id }),
    }),
  getWhisperSettings: () => request("/api/settings/whisper"),
  saveWhisperSettings: (data) =>
    request("/api/settings/whisper", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  voiceStats: () => request("/api/stats/voice"),
  replicationStatus: () => request("/api/replication/status"),
  saveReplicationSettings: (data) =>
    request("/api/replication/settings", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  runReplication: () =>
    request("/api/replication/run", { method: "POST" }),
  cancelReplication: () =>
    request("/api/replication/cancel", { method: "POST" }),
  replicationRuns: () => request("/api/replication/runs"),
  deleteReplicationRun: (run_id, confirm = false) =>
    request(
      `/api/replication/runs/${run_id}${confirm ? "?confirm=true" : ""}`,
      { method: "DELETE" },
    ),
  replicationRunLog: (run_id) =>
    request(`/api/replication/runs/${run_id}/log`),
  ragStatus: () => request("/api/rag/status"),
  ragStats: () => request("/api/rag/stats"),
  ragIndexAll: () => request("/api/rag/index-all", { method: "POST" }),
  ragSearch: (query, chat_id = null, limit = 5) =>
    request("/api/rag/search", {
      method: "POST",
      body: JSON.stringify({ query, chat_id, limit }),
    }),
  ragClear: () => request("/api/rag/clear?confirm=true", { method: "DELETE" }),
  getRagSettings: () => request("/api/settings/rag"),
  saveRagSettings: (data) =>
    request("/api/settings/rag", {
      method: "POST",
      body: JSON.stringify(data),
    }),
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
