import React, { useRef } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

const SPAN_CLASS = {
  1: "",
  2: "md:col-span-2",
  3: "md:col-span-2 xl:col-span-3",
  4: "md:col-span-2 xl:col-span-4",
};

export default function SortableTile({ id, colSpan = 1, onResize, children }) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
    isOver,
  } = useSortable({ id });

  const resize = useRef(null);

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  const stateClass = isDragging
    ? "opacity-50 scale-[.97] rotate-[-0.5deg] z-10"
    : isOver
    ? "ring-2 ring-indigo-500 dark:ring-indigo-400 scale-[1.02] rounded-xl"
    : "";

  function handleDown(e) {
    if (!onResize) return;
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    const rect = e.currentTarget.parentElement.getBoundingClientRect();
    resize.current = {
      startX: e.clientX,
      startSpan: colSpan,
      unit: rect.width / colSpan,
    };
  }

  function handleMove(e) {
    const r = resize.current;
    if (!r) return;
    const delta = Math.round((e.clientX - r.startX) / r.unit);
    const next = Math.min(4, Math.max(1, r.startSpan + delta));
    if (next !== colSpan) onResize(id, next);
  }

  function handleUp(e) {
    resize.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      // ignore
    }
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`relative group ${SPAN_CLASS[colSpan] || ""} ${stateClass}
                  transition-transform duration-150`}
    >
      {children({ dragHandleProps: { ...attributes, ...listeners } })}
      {onResize && (
        <div
          onPointerDown={handleDown}
          onPointerMove={handleMove}
          onPointerUp={handleUp}
          title="Потяни, чтобы изменить ширину"
          className="absolute -right-1 top-1/2 -translate-y-1/2 z-20
                     w-2 h-14 rounded-full cursor-ew-resize touch-none
                     bg-light-border2 dark:bg-dark-border2
                     opacity-0 pointer-events-none
                     group-hover:opacity-70 group-hover:pointer-events-auto
                     hover:!opacity-100 transition-opacity duration-150"
        />
      )}
    </div>
  );
}
