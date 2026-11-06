// How a streamed answer is revealed: the server sends text in uneven bursts, the screen shows it at an even pace that
// speeds up when the backlog grows (so it never falls far behind) and slows to a natural rhythm when it is small.
// Pure functions (no React) so they can be tested; the hook that uses them is in smooth-text.ts.

export const FRAME_MS = 33; // ~30 reveals per second: smooth, and cheap enough to re-render markdown each time
export const FRAME_MS_LONG = 50; // very long answers re-render a lot of markdown: a little less often
export const LONG = 6000;

/** How far to reveal next. `shown` characters are on screen, `target` is everything received so far. */
export function nextShown(shown: number, target: string): number {
  const behind = target.length - shown;
  if (behind <= 0) return target.length;
  // 3 characters per reveal when nearly caught up (a calm ~90 chars/s), more when the backlog is big: it empties in about a second
  const step = Math.max(3, Math.ceil(behind * 0.12));
  let next = Math.min(target.length, shown + step);
  if (next < target.length) {
    // finish the word: words appear whole, not letter fragments (look ahead a few characters for a space or a line break)
    const tail = target.slice(next, next + 12);
    const m = /[\s]/.exec(tail);
    if (m) next += m.index + 1;
    else if (tail.length < 12) next = target.length;  // the last word of what has arrived: show it whole
  }
  // never cut a surrogate pair in half (emoji)
  const c = target.charCodeAt(next - 1);
  if (next < target.length && c >= 0xd800 && c <= 0xdbff) next += 1;
  return Math.min(next, target.length);
}

/** Length of the common start of two strings: when the answer is edited or regenerated the reveal starts from what still matches. */
export function commonPrefix(a: string, b: string): number {
  const n = Math.min(a.length, b.length);
  let i = 0;
  while (i < n && a.charCodeAt(i) === b.charCodeAt(i)) i++;
  return i;
}
