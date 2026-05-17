import { useCallback, useState } from "react";

const ORDER_KEY = "ai-dashboard-layout";
const SIZES_KEY = "ai-dashboard-sizes";

export const DEFAULT_ORDER = [
  "metrics-received",
  "metrics-sent",
  "metrics-quality",
  "metrics-rag",
  "activity-chart",
  "model-status",
  "llama-server",
  "vector-memory",
  "message-feed",
  "reply-panel",
];

export const DEFAULT_SIZES = {
  "metrics-received": { w: 336, h: 168 },
  "metrics-sent": { w: 336, h: 168 },
  "metrics-quality": { w: 336, h: 168 },
  "metrics-rag": { w: 336, h: 168 },
  "activity-chart": { w: 688, h: 250 },
  "model-status": { w: 688, h: 470 },
  "llama-server": { w: 336, h: 250 },
  "vector-memory": { w: 688, h: 360 },
  "message-feed": { w: 688, h: 440 },
  "reply-panel": { w: 688, h: 440 },
};

export const MIN_SIZE = { w: 240, h: 120 };

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

function loadSizes() {
  const sizes = { ...DEFAULT_SIZES };
  try {
    const stored = JSON.parse(localStorage.getItem(SIZES_KEY));
    if (stored && typeof stored === "object") {
      for (const id of Object.keys(sizes)) {
        const s = stored[id];
        if (s && typeof s.w === "number" && typeof s.h === "number") {
          sizes[id] = { w: s.w, h: s.h };
        }
      }
    }
  } catch {
    // ignore
  }
  return sizes;
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
  const [sizes, setSizes] = useState(loadSizes);

  const reorder = useCallback((next) => {
    setOrder(next);
    write(ORDER_KEY, next);
  }, []);

  const setSize = useCallback((id, size) => {
    setSizes((prev) => {
      const next = { ...prev, [id]: size };
      write(SIZES_KEY, next);
      return next;
    });
  }, []);

  const reset = useCallback(() => {
    setOrder(DEFAULT_ORDER);
    setSizes({ ...DEFAULT_SIZES });
    write(ORDER_KEY, DEFAULT_ORDER);
    write(SIZES_KEY, {});
  }, []);

  return { order, reorder, sizes, setSize, reset };
}
