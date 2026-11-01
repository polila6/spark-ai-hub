"use client";
import { Brain, Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { Guide, StepsEmpty } from "@/components/guide";
import { Badge, Button, Card, CardBody, CardHeader, Field, Input, PageHeader, Switch, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { dt } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useMe, useToast } from "@/lib/providers";

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
          <CardHeader title={L("Memorias", "Memories")} subtitle={L("Datos que pediste recordar («recuerda que…»)", "Facts you asked to remember (“remember that…”)")}
            actions={<Switch checked={enabled} onChange={(v) => setPrefs({ memory_enabled: v })} label={enabled ? L("Activada", "On") : L("Desactivada", "Off")} />} />
          <CardBody className="space-y-3">
            <form className="flex gap-2" onSubmit={async (e) => { e.preventDefault(); if (!nm.trim()) return; await api("/api/v1/memories", { method: "POST", json: { content: nm } }); setNm(""); mutate(); }}>
              <Input value={nm} onChange={(e) => setNm(e.target.value)} placeholder={L("Añadir: «Mi grupo es el de Diseño Sostenible»", "Add: “My group is Sustainable Design”")} />
              <Button type="submit" variant="primary" disabled={!nm.trim()}><Plus className="h-4 w-4" /></Button>
            </form>
            {data && !data.items.length ? (
              <StepsEmpty icon={<Brain className="h-5 w-5" />} title={L("Sin memorias todavía", "No memories yet")} steps={[L("En el chat di «recuerda que…» y el asistente lo guardará.", "In the chat say “remember that…” and the assistant saves it."), L("O añádelas aquí arriba.", "Or add them above.")]} />
            ) : (
              <ul className="divide-y divide-border/60">
                {data?.items.map((m: any) => (
                  <li key={m.id} className="flex items-start gap-2 py-2">
                    {edit?.id === m.id ? (
                      <form className="flex flex-1 gap-2" onSubmit={async (e) => { e.preventDefault(); await api(`/api/v1/memories/${m.id}`, { method: "PATCH", json: { content: edit?.content } }); setEdit(null); mutate(); }}>
                        <Input autoFocus value={edit?.content ?? ""} onChange={(e) => setEdit({ id: m.id, content: e.target.value })} /><Button type="submit" size="sm" variant="primary">{L("Guardar", "Save")}</Button></form>
                    ) : (
                      <div className="min-w-0 flex-1"><p className="text-sm">{m.content}</p><p className="text-[10px] text-subtle">{dt(m.created_at, lang)} · <Badge>{m.source === "assistant" ? L("desde el chat", "from chat") : m.source === "mcp" ? "MCP" : L("manual", "manual")}</Badge></p></div>
                    )}
                    <Button size="icon" variant="ghost" aria-label={L("Editar", "Edit")} onClick={() => setEdit({ id: m.id, content: m.content })}><Pencil className="h-3.5 w-3.5" /></Button>
                    <Button size="icon" variant="ghost" aria-label={L("Borrar", "Delete")} onClick={async () => { await api(`/api/v1/memories/${m.id}`, { method: "DELETE" }); mutate(); }}><Trash2 className="h-3.5 w-3.5" /></Button>
                  </li>
                ))}
              </ul>
            )}
            {data?.items.length > 0 && <div className="flex justify-end"><Button size="sm" variant="ghost" onClick={async () => { if (window.confirm(L("¿Borrar toda la memoria?", "Delete all memory?"))) { await api("/api/v1/memories", { method: "DELETE" }); mutate(); } }}>{L("Borrar todo", "Delete all")}</Button></div>}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
