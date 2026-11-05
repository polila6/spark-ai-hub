"use client";
// An assistant answer as markdown where the files it produced are blue links that open them in place
// (canvas, workspace file, or the file viewer). See lib/deliverables.ts for what gets linked.
import { useRouter } from "next/navigation";
import { useMemo, useRef } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import { hubFile, useFileViewer } from "@/components/file-viewer";
import { deliverablesOf, linkDeliverables, parseHref } from "@/lib/deliverables";

const urlTransform = (u: string) => (u.startsWith("sah:") ? u : defaultUrlTransform(u));

export function AnswerMarkdown({ content, tools, components, onCanvas, onWs }: {
  content: string; tools?: any[]; components?: any; onCanvas?: (id: string, v?: number) => void; onWs?: (path: string) => void;
}) {
  const viewer = useFileViewer();
  const router = useRouter();
  const items = useMemo(() => deliverablesOf(tools), [tools]);
  const text = useMemo(() => linkDeliverables(content, items), [content, items]);
  const act = useRef({ viewer, router, onCanvas, onWs });
  act.current = { viewer, router, onCanvas, onWs };
  const A = useMemo(() => function Link({ href, children }: any) {
    const d = parseHref(href, items);
    if (!d) return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
    const open = () => {
      const a = act.current;
      if (d.kind === "canvas") a.onCanvas?.(d.ref);
      else if (d.kind === "file") a.viewer.open(hubFile(d.file ?? { id: d.ref, filename: d.name }));
      else if (a.onWs) a.onWs(d.ref);
      else a.router.push(`/workspace#open=${encodeURIComponent(d.ref)}`);
    };
    return <button type="button" onClick={open} title={d.name}
      className="cursor-pointer text-blue-500 underline decoration-blue-500/40 underline-offset-2 hover:text-blue-400 hover:decoration-blue-400">{children}</button>;
  }, [items]);
  return <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={urlTransform} components={{ ...components, a: A }}>{text}</ReactMarkdown>;
}
