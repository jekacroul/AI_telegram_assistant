import { useCallback, useState } from "react";

const STORAGE_KEY = "ai-dashboard-layout";

export const DEFAULT_ORDER = [
  "metrics-received",
  "metrics-sent",
  "metrics-quality",
  "metrics-rag",
  "activity-chart",
  "model-status",
  "message-feed",
  "reply-panel",
];

function loadOrder() {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (Array.isArray(stored) && stored.length) {
      const known = stored.filter((id) => DEFAULT_ORDER.includes(id));
      const missing = DEFAULT_ORDER.filter((id) => !known.includes(id));
      return [...known, ...missing];
    }
  } catch {
    // ignore malformed storage
  }
  return DEFAULT_ORDER;
}

export function useTileLayout() {
  const [order, setOrder] = useState(loadOrder);

  const persist = (next) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      // ignore
    }
  };

  const reorder = useCallback((next) => {
    setOrder(next);
    persist(next);
  }, []);

  const reset = useCallback(() => {
    setOrder(DEFAULT_ORDER);
    persist(DEFAULT_ORDER);
  }, []);

  return { order, reorder, reset };
}
