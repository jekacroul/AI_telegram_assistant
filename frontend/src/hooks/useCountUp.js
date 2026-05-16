import { useEffect, useRef, useState } from "react";

export function useCountUp(target, duration = 600) {
  const [value, setValue] = useState(0);
  const raf = useRef(0);

  useEffect(() => {
    const num = Number(target) || 0;
    const start = performance.now();
    cancelAnimationFrame(raf.current);
    const tick = (now) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(num * eased);
      if (t < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [target, duration]);

  return value;
}
