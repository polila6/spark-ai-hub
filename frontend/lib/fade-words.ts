// Each word that arrives while an answer streams fades in as it appears, following the real rhythm of the model: words that come one
// by one fade one by one, and a burst of words is staggered over a fraction of a second instead of popping. Nothing is held back:
// what has arrived is on screen. A rehype plugin (pure, no React) wraps the words of the text in <span class="tok"> with a delay.
// Words already on screen when the message appears (a reloaded page) and code are left alone.

export type FadeState = { delays: number[] };  // per word of the message, in order: ms of delay, or -1 = no animation
export const STAGGER_MS = 22;
export const MAX_STAGGER_MS = 360;
const SKIP = new Set(["pre", "code", "script", "style", "svg"]);

type Node = { type: string; tagName?: string; value?: string; properties?: Record<string, any>; children?: Node[] };

/** Wrap the words of the tree. `startLen` = how many words the message had before this update (the new ones are after it). */
export function fadeWords(tree: Node, state: FadeState, startLen: number): number {
  let idx = 0;
  const walk = (node: Node) => {
    if (!node.children) return;
    const out: Node[] = [];
    for (const child of node.children) {
      if (child.type === "element" && SKIP.has(child.tagName ?? "")) { out.push(child); continue; }
      if (child.type !== "text" || !child.value) {
        walk(child);
        out.push(child);
        continue;
      }
      for (const part of child.value.split(/(\s+)/)) {
        if (!part) continue;
        if (/^\s+$/.test(part)) { out.push({ type: "text", value: part }); continue; }
        const i = idx++;
        if (state.delays[i] === undefined) state.delays[i] = Math.min(Math.max(0, i - startLen) * STAGGER_MS, MAX_STAGGER_MS);
        const d = state.delays[i];
        out.push(d < 0 ? { type: "text", value: part }
          : { type: "element", tagName: "span", properties: { className: ["tok"], style: `--d:${d}ms` }, children: [{ type: "text", value: part }] });
      }
    }
    node.children = out;
  };
  walk(tree);
  return idx;
}

export const rehypeFadeWords = (opts: { state: FadeState; startLen: number }) => (tree: Node) => { fadeWords(tree, opts.state, opts.startLen); };

/** Words (runs of non-space characters) in a text: used to mark what was already there when the message appeared. */
export const countWords = (text: string) => (text.match(/\S+/g) ?? []).length;
