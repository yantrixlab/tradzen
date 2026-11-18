import { useEffect, useRef, useState } from 'react';

/**
 * The width in pixels of the box the returned ref is put on (it follows resizes). A chart drawn in a fixed viewBox shrinks with its card, and on a phone its text
 * becomes unreadable; drawn to `min(designWidth, measured width)` it keeps its text size and only the plot gets narrower.
 */
export function useBoxWidth<T extends HTMLElement = HTMLDivElement>(initial = 640, min = 240) {
  const ref = useRef<T>(null);
  const [w, setW] = useState(initial);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => { const x = Math.round(el.clientWidth); if (x > 0) setW(Math.max(min, x)); };
    read();
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [min]);
  return [ref, w] as const;
}
