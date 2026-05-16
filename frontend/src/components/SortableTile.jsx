import React from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

export default function SortableTile({ id, colSpan = 1, children }) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
    isOver,
  } = useSortable({ id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  const spanClass =
    colSpan >= 4
      ? "md:col-span-2 xl:col-span-4"
      : colSpan === 2
      ? "md:col-span-2"
      : "";

  const stateClass = isDragging
    ? "opacity-50 scale-[.97] rotate-[-0.5deg] z-10"
    : isOver
    ? "ring-2 ring-indigo-500 dark:ring-indigo-400 scale-[1.02] rounded-xl"
    : "";

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`${spanClass} ${stateClass} transition-transform duration-150`}
    >
      {children({ dragHandleProps: { ...attributes, ...listeners } })}
    </div>
  );
}
