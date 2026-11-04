"use client";
// Follow a growing chat only while the reader is at the end: moving up, by any means, stops the following at once, so
// new tokens never pull the view back down. It resumes on returning to the end or with the "go to the end" button.
import { ArrowDown } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useL } from "@/lib/i18n";

export function useStickToBottom(ref: React.RefObject<HTMLElement | null>, content: unknown, count: number) {
  const stick = useRef(true);
  const lastTop = useRef(0);
  const lastCount = useRef(count);
  const [away, setAway] = useState(false);
  const jump = useCallback(() => {
    const el = ref.current;
    stick.current = true;
    setAway(false);
    if (el) { el.scrollTop = el.scrollHeight; lastTop.current = el.scrollTop; }
  }, [ref]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const leave = () => { stick.current = false; setAway(true); };
    const onWheel = (e: WheelEvent) => { if (e.deltaY < 0) leave(); };
    const onScroll = () => {
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
      if (dist <= 4) { stick.current = true; setAway(false); }  // back at the end
      else if (el.scrollTop < lastTop.current) leave();  // moved up: keyboard, scrollbar, touch
      lastTop.current = el.scrollTop;
    };
    el.addEventListener("wheel", onWheel, { passive: true });
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => { el.removeEventListener("wheel", onWheel); el.removeEventListener("scroll", onScroll); };
  }, [ref]);
  useLayoutEffect(() => {
    if (count > lastCount.current) stick.current = true;  // a new message: show it
    lastCount.current = count;
    const el = ref.current;
    if (el && stick.current) { el.scrollTop = el.scrollHeight; lastTop.current = el.scrollTop; setAway(false); }
  }, [ref, content, count]);
  return { away, jump };
}

/** Floating "go to the end" button: place it right after the scrolling list. */
export function JumpToEnd({ show, onClick }: { show: boolean; onClick: () => void }) {
  const L = useL();
  if (!show) return null;
  return (
    <div className="relative h-0">
      <button type="button" onClick={onClick} className="absolute -top-11 left-1/2 z-10 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1.5 text-xs font-medium text-fg shadow-card hover:bg-elevated">
        <ArrowDown className="h-3.5 w-3.5" />{L("Ir al final", "Go to the end")}
      </button>
    </div>
  );
}
