"use client";
// useSmoothText(text, streaming): the text to show now. While an answer streams, new text is revealed at an even, adaptive
// pace instead of popping in with each network burst; when it ends, whatever is still pending finishes revealing. A message
// that was never streamed here (history, reload) is shown whole at once, and so is everything when the person prefers
// reduced motion.
import { useEffect, useRef, useState } from "react";
import { commonPrefix, FRAME_MS, FRAME_MS_LONG, LONG, nextShown } from "./smooth-step";

export function useSmoothText(text: string, streaming: boolean): { text: string; active: boolean } {
  // what is already there when the component appears (a page reloaded in the middle of an answer) is shown at once;
  // only text that arrives afterwards is revealed gradually
  const [n, setN] = useState(text.length);
  const s = useRef({ n: text.length, text, animated: streaming, last: 0, raf: 0, shownText: text, streaming });
  s.current.text = text;
  s.current.streaming = streaming;

  useEffect(() => {
    const st = s.current;
    let reduced = false;
    try { reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { /* no matchMedia */ }
    if (streaming && !reduced) st.animated = true;
    if (!st.animated || reduced) {  // not an answer being streamed here: show it whole
      st.n = text.length; st.shownText = text; setN(text.length);
      return;
    }
    // edited / regenerated: start again from what still matches what is already on screen
    if (st.n > text.length || (st.n > 0 && !text.startsWith(st.shownText.slice(0, st.n)))) {
      st.n = Math.min(st.n, commonPrefix(st.shownText, text));
      setN(st.n);
    }
    if (st.raf) return;
    const tick = (now: number) => {
      const cur = s.current;
      if (cur.n >= cur.text.length) { cur.raf = 0; if (!cur.streaming) cur.animated = false; return; }  // caught up: idle until more arrives
      if (now - cur.last >= (cur.text.length > LONG ? FRAME_MS_LONG : FRAME_MS)) {
        cur.n = nextShown(cur.n, cur.text);
        cur.shownText = cur.text;
        cur.last = now;
        setN(cur.n);
      }
      cur.raf = requestAnimationFrame(tick);
    };
    st.raf = requestAnimationFrame(tick);
  }, [text, streaming]);

  useEffect(() => () => { if (s.current.raf) cancelAnimationFrame(s.current.raf); s.current.raf = 0; }, []);

  const st = s.current;
  if (!st.animated) return { text, active: false };
  const shown = text.slice(0, Math.min(n, text.length));
  return { text: shown, active: streaming || shown.length < text.length };
}
