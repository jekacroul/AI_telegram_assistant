import { useCallback, useState } from "react";

const ORDER_KEY = "ai-dashboard-layout";
const SPANS_KEY = "ai-dashboard-spans";

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
    const stored = JSON.parse(localStorage.getItem(ORDER_KEY));
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

function loadSpans() {
  try {
    const stored = JSON.parse(localStorage.getItem(SPANS_KEY));
    if (stored && typeof stored === "object") return stored;
  } catch {
    // ignore
  }
  return {};
}

function write(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // ignore
  }
}

export function useTileLayout() {
  const [order, setOrder] = useState(loadOrder);
  const [spans, setSpans] = useState(loadSpans);

  const reorder = useCallback((next) => {
    setOrder(next);
    write(ORDER_KEY, next);
  }, []);

  const setSpan = useCallback((id, span) => {
    setSpans((prev) => {
      const next = { ...prev, [id]: span };
      write(SPANS_KEY, next);
      return next;
    });
  }, []);

  const reset = useCallback(() => {
    setOrder(DEFAULT_ORDER);
    setSpans({});
    write(ORDER_KEY, DEFAULT_ORDER);
    write(SPANS_KEY, {});
  }, []);

  return { order, reorder, spans, setSpan, reset };
}
