"use client";
import { Brain, Download, Pencil, Plus, Trash2, Upload } from "lucide-react";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { Guide, StepsEmpty } from "@/components/guide";
import { Badge, Button, Card, CardBody, CardHeader, Field, Input, PageHeader, Select, Switch, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { dt } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useMe, useToast } from "@/lib/providers";

const CAT: Record<string, [string, string]> = {
  profile: ["Sobre mí", "About me"], preference: ["Preferencias", "Preferences"], work: ["Mi trabajo", "My work"],
  project: ["Proyectos", "Projects"], context: ["Contexto", "Context"], other: ["Otros", "Other"],
};

export default function MemoryPage() {
  const L = useL();
  const { lang } = useI18n();
  const { me, setPrefs } = useMe();
  const { push } = useToast();
  const { data, mutate } = useSWR("/api/v1/memories");
  const [about, setAbout] = useState("");
  const [instr, setInstr] = useState("");
  const [nm, setNm] = useState("");
  const [edit, setEdit] = useState<{ id: string; content: string } | null>(null);
  useEffect(() => { setAbout(me?.preferences?.about_me ?? ""); setInstr(me?.preferences?.custom_instructions ?? ""); }, [me]);
  const enabled = me?.preferences?.memory_enabled ?? true;
  const auto = me?.preferences?.memory_auto ?? true;
  const [cat, setCat] = useState("");
  const [imp, setImp] = useState("");
  return (
    <div>
      <PageHeader title={L("Memoria y personalización", "Memory and personalisation")} subtitle={L("Lo que el asistente sabe de ti en todas las conversaciones", "What the assistant knows about you in every chat")} icon={<Brain className="h-5 w-5" />} />
      <Guide id="memory" />
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title={L("Personalización", "Personalisation")} subtitle={L("Se añade a cada conversación del chat web", "Added to every web chat")} />
          <CardBody className="space-y-3">
            <Field label={L("Sobre ti", "About you")} hint={L("Tu rol, área, herramientas, nivel… (máx. 3000 caracteres)", "Your role, field, tools, level… (max 3000 chars)")}>
              <Textarea rows={4} maxLength={3000} value={about} onChange={(e) => setAbout(e.target.value)} placeholder={L("Investigadora en ingeniería del diseño. Trabajo con Python, Rhino y KeyShot.", "Design engineering researcher. I work with Python, Rhino and KeyShot.")} /></Field>
            <Field label={L("Cómo quieres las respuestas", "How you want answers")} hint={L("Idioma, tono, formato, extensión…", "Language, tone, format, length…")}>
              <Textarea rows={4} maxLength={5000} value={instr} onChange={(e) => setInstr(e.target.value)} placeholder={L("Responde en español, de forma concisa, con ejemplos de código cuando aplique.", "Answer in English, concisely, with code examples when relevant.")} /></Field>
            <div className="flex justify-end"><Button variant="primary" onClick={async () => { await setPrefs({ about_me: about, custom_instructions: instr }); push("ok", L("Guardado", "Saved")); }}>{L("Guardar", "Save")}</Button></div>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title={L("Memorias", "Memories")} subtitle={L("Lo que el asistente va aprendiendo de ti, y lo que le pediste recordar", "What the assistant learns about you, and what you asked it to remember")}
            actions={<Switch checked={enabled} onChange={(v) => setPrefs({ memory_enabled: v })} label={enabled ? L("Activada", "On") : L("Desactivada", "Off")} />} />
          <CardBody className="space-y-3">
            <Switch checked={auto} onChange={(v) => setPrefs({ memory_auto: v })}
              label={L("Aprender automáticamente de mis chats", "Learn automatically from my chats")} />
            <p className="-mt-1 text-xs text-muted">{L("Tras cada respuesta, el asistente apunta lo que sea duradero (tu trabajo, tus herramientas, cómo te gustan las respuestas), fusiona lo repetido y corrige lo que cambie. Nunca guarda contraseñas, claves ni números de identidad o de tarjeta, y no toca lo que escribiste tú.",
              "After each answer the assistant notes what is lasting (your work, your tools, how you like answers), merges repeats and corrects what changes. It never stores passwords, keys or identity or card numbers, and never touches what you wrote yourself.")}</p>
            <div className="flex flex-wrap items-center gap-2">
              <Select className="h-8 w-auto text-xs" value={cat} onChange={(e) => setCat(e.target.value)} aria-label={L("Categoría", "Category")}>
                <option value="">{L("Todas las categorías", "All categories")}</option>
                {Object.entries(CAT).map(([k, v]) => <option key={k} value={k}>{L(v[0], v[1])}</option>)}
              </Select>
              <span className="ml-auto flex flex-wrap gap-1.5">
                <a href={`/api/v1/memories/export?format=markdown&lang=${lang}`} download><Button size="sm" variant="outline" type="button"><Download className="h-3.5 w-3.5" />{L("Para otra IA (.md)", "For another AI (.md)")}</Button></a>
                <a href="/api/v1/memories/export?format=json" download><Button size="sm" variant="outline" type="button"><Download className="h-3.5 w-3.5" />JSON</Button></a>
              </span>
            </div>
            <form className="flex gap-2" onSubmit={async (e) => { e.preventDefault(); if (!nm.trim()) return; await api("/api/v1/memories", { method: "POST", json: { content: nm } }); setNm(""); mutate(); }}>
              <Input value={nm} onChange={(e) => setNm(e.target.value)} placeholder={L("Añadir: «Mi grupo es el de Diseño Sostenible»", "Add: “My group is Sustainable Design”")} />
              <Button type="submit" variant="primary" disabled={!nm.trim()}><Plus className="h-4 w-4" /></Button>
            </form>
            {data && !data.items.length ? (
              <StepsEmpty icon={<Brain className="h-5 w-5" />} title={L("Sin memorias todavía", "No memories yet")} steps={[L("En el chat di «recuerda que…» y el asistente lo guardará.", "In the chat say “remember that…” and the assistant saves it."), L("O añádelas aquí arriba.", "Or add them above.")]} />
            ) : (
              <ul className="divide-y divide-border/60">
                {data?.items.filter((m: any) => !cat || m.category === cat).map((m: any) => (
                  <li key={m.id} className="flex items-start gap-2 py-2">
                    {edit?.id === m.id ? (
                      <form className="flex flex-1 gap-2" onSubmit={async (e) => { e.preventDefault(); await api(`/api/v1/memories/${m.id}`, { method: "PATCH", json: { content: edit?.content } }); setEdit(null); mutate(); }}>
                        <Input autoFocus value={edit?.content ?? ""} onChange={(e) => setEdit({ id: m.id, content: e.target.value })} /><Button type="submit" size="sm" variant="primary">{L("Guardar", "Save")}</Button></form>
                    ) : (
                      <div className="min-w-0 flex-1"><p className="text-sm">{m.content}</p><p className="text-[10px] text-subtle">{dt(m.created_at, lang)} · <Badge>{m.source === "auto" ? L("aprendida", "learned") : m.source === "assistant" ? L("desde el chat", "from chat") : m.source === "mcp" ? "MCP" : m.source === "import" ? L("importada", "imported") : L("escrita por ti", "written by you")}</Badge> <Badge>{L(...(CAT[m.category] ?? CAT.other))}</Badge> <span title={L("Importancia", "Importance")}>{"★".repeat(m.importance ?? 3)}</span>{m.mentions > 1 && <span> · {L(`confirmada ${m.mentions} veces`, `confirmed ${m.mentions} times`)}</span>}</p></div>
                    )}
                    <Button size="icon" variant="ghost" aria-label={L("Editar", "Edit")} onClick={() => setEdit({ id: m.id, content: m.content })}><Pencil className="h-3.5 w-3.5" /></Button>
                    <Button size="icon" variant="ghost" aria-label={L("Borrar", "Delete")} onClick={async () => { await api(`/api/v1/memories/${m.id}`, { method: "DELETE" }); mutate(); }}><Trash2 className="h-3.5 w-3.5" /></Button>
                  </li>
                ))}
              </ul>
            )}
            <details className="rounded-xl border border-border p-3 text-sm">
              <summary className="flex cursor-pointer items-center gap-1.5 font-medium"><Upload className="h-3.5 w-3.5" />{L("Importar memoria de otra IA o de un archivo", "Import memory from another AI or a file")}</summary>
              <ol className="mt-2 list-decimal space-y-0.5 pl-5 text-xs text-muted">
                <li>{L("En la otra IA pídele: «Lista todo lo que recuerdas de mí, una frase por línea que empiece por «- »».", "In the other AI ask: “List everything you remember about me, one sentence per line starting with “- ”.”")}</li>
                <li>{L("Pega aquí su respuesta (o el contenido de un JSON exportado desde aquí).", "Paste its answer here (or the content of a JSON exported from here).")}</li>
                <li>{L("Pulsa Importar: se añade lo nuevo y se salta lo que ya tienes.", "Click Import: new facts are added and ones you already have are skipped.")}</li>
              </ol>
              <Textarea className="mt-2" rows={4} value={imp} onChange={(e) => setImp(e.target.value)} placeholder="- Soy ingeniera de robótica&#10;- Prefiero respuestas cortas" />
              <div className="mt-2 flex justify-end"><Button size="sm" variant="primary" disabled={!imp.trim()} onClick={async () => {
                try {
                  const t = imp.trim(); let body: any = { text: t };
                  if (t.startsWith("{")) { try { body = { data: JSON.parse(t) }; } catch { /* plain text */ } }
                  const r = await api("/api/v1/memories/import", { method: "POST", json: body });
                  push("ok", L(`Importadas ${r.added}, repetidas ${r.skipped}`, `Imported ${r.added}, skipped ${r.skipped}`)); setImp(""); mutate();
                } catch (e: any) { push("error", e.message); }
              }}>{L("Importar", "Import")}</Button></div>
            </details>
            {data?.items.length > 0 && <div className="flex justify-end"><Button size="sm" variant="ghost" onClick={async () => { if (window.confirm(L("¿Borrar toda la memoria?", "Delete all memory?"))) { await api("/api/v1/memories", { method: "DELETE" }); mutate(); } }}>{L("Borrar todo", "Delete all")}</Button></div>}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
