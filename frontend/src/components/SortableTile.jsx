import React, { useRef } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { MIN_SIZE } from "../hooks/useTileLayout.js";

export default function SortableTile({ id, size, expanded, onResize, children }) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
    isOver,
  } = useSortable({ id });

  const wrapperRef = useRef(null);
  const resize = useRef(null);

  const setRefs = (node) => {
    wrapperRef.current = node;
    setNodeRef(node);
  };

  const stateClass = isDragging
    ? "opacity-50 scale-[.98] rotate-[-0.4deg] z-10"
    : isOver
    ? "ring-2 ring-indigo-500 dark:ring-indigo-400"
    : "";

  function startResize(e, axis) {
    if (!onResize) return;
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    const maxW =
      wrapperRef.current?.parentElement?.clientWidth || window.innerWidth;
    resize.current = {
      x: e.clientX,
      y: e.clientY,
      w: size.w,
      h: size.h,
      axis,
      maxW,
    };
  }

  function moveResize(e) {
    const r = resize.current;
    if (!r) return;
    let w = r.w;
    let h = r.h;
    if (r.axis.includes("x")) {
      w = Math.min(r.maxW, Math.max(MIN_SIZE.w, r.w + (e.clientX - r.x)));
    }
    if (r.axis.includes("y")) {
      h = Math.max(MIN_SIZE.h, r.h + (e.clientY - r.y));
    }
    onResize(id, { w: Math.round(w), h: Math.round(h) });
  }

  function endResize(e) {
    resize.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      // ignore
    }
  }

  const handleBase =
    "absolute z-20 opacity-0 pointer-events-none touch-none " +
    "group-hover:opacity-100 group-hover:pointer-events-auto " +
    "transition-opacity duration-150";

  return (
    <div
      ref={setRefs}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        width: size.w,
        height: expanded ? "auto" : size.h,
        minHeight: expanded ? size.h : undefined,
        maxWidth: "100%",
      }}
      className={`relative group flex-shrink-0 ${stateClass}`}
    >
      {children({ dragHandleProps: { ...attributes, ...listeners } })}

      {onResize && (
        <>
          {/* right edge — width */}
          <div
            onPointerDown={(e) => startResize(e, "x")}
            onPointerMove={moveResize}
            onPointerUp={endResize}
            title="Ширина"
            className={`${handleBase} top-3 bottom-6 right-0 w-1.5
                        cursor-ew-resize flex items-center justify-center`}
          >
            <span className="w-1 h-8 rounded-full bg-light-border2 dark:bg-dark-border2" />
          </div>
          {!expanded && (
            <>
              {/* bottom edge — height */}
              <div
                onPointerDown={(e) => startResize(e, "y")}
                onPointerMove={moveResize}
                onPointerUp={endResize}
                title="Высота"
                className={`${handleBase} left-3 right-6 bottom-0 h-1.5
                            cursor-ns-resize flex items-center justify-center`}
              >
                <span className="h-1 w-8 rounded-full bg-light-border2 dark:bg-dark-border2" />
              </div>
              {/* corner — both */}
              <div
                onPointerDown={(e) => startResize(e, "xy")}
                onPointerMove={moveResize}
                onPointerUp={endResize}
                title="Размер"
                className={`${handleBase} bottom-0 right-0 w-5 h-5
                            cursor-nwse-resize flex items-end justify-end p-1`}
              >
                <span
                  className="w-2.5 h-2.5 border-r-2 border-b-2
                             border-light-border2 dark:border-dark-border2 rounded-br-sm"
                />
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
