"use client";
import { Download, ExternalLink, Globe, Lock, Plus, Puzzle, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR from "swr";
import { Guide, StepsEmpty } from "@/components/guide";
import { Badge, Button, Card, CardBody, CardHeader, Field, Input, Modal, Notice, PageHeader, Skeleton, Switch, Tabs, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { isManager, useMe, useToast } from "@/lib/providers";

function SkillView({ id, onClose }: { id: string; onClose: () => void }) {
  const L = useL();
  const { data } = useSWR(`/api/v1/skills/${id}`);
  const [file, setFile] = useState<string | null>(null);
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} wide title={data?.name ?? "…"} description={data?.description}>
      {!data ? <Skeleton className="h-40" /> : (
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap gap-2 text-xs">
            {data.source_url && <a className="text-accent" href={data.source_url} target="_blank" rel="noreferrer noopener">{data.source_url} <ExternalLink className="inline h-3 w-3" /></a>}
            {data.license && <Badge>{data.license.slice(0, 60)}</Badge>}
          </div>
          {data.files.length > 0 && <div className="flex flex-wrap gap-1">{["SKILL.md", ...data.files].map((f: string) => <button key={f} onClick={() => setFile(f === "SKILL.md" ? null : f)} className={`rounded-md border px-2 py-0.5 text-[11px] ${(file ?? "SKILL.md") === f ? "border-accent text-accent" : "border-border text-muted"}`}>{f}</button>)}</div>}
          {file ? <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-lg bg-bg p-3 font-mono text-[11px]">{data.file_contents[file]}</pre>
            : <div className="prose-chat max-h-[60vh] overflow-y-auto rounded-lg bg-bg p-3"><ReactMarkdown remarkPlugins={[remarkGfm]}>{data.content}</ReactMarkdown></div>}
          <Notice tone="muted">{L("Las skills son instrucciones: el asistente las lee y las sigue con sus herramientas. Los scripts que incluyan se guardan como referencia y nunca se ejecutan en el Spark.", "Skills are instructions: the assistant reads and follows them with its tools. Bundled scripts are kept as reference and never executed on the Spark.")}</Notice>
        </div>
      )}
    </Modal>
  );
}

function Installed() {
  const L = useL();
  const { lang } = useI18n();
  const { me } = useMe();
  const { push } = useToast();
  const { data, mutate } = useSWR("/api/v1/skills");
  const [view, setView] = useState<string | null>(null);
  async function patch(id: string, json: any) {
    try { await api(`/api/v1/skills/${id}`, { method: "PATCH", json }); mutate(); } catch (e: any) { push("error", e.message); }
  }
  if (!data) return <Skeleton className="h-40" />;
  return (
    <div className="space-y-3">
      {!data.settings.enabled && <Notice tone="warn">{L("El administrador ha desactivado las skills en el chat.", "The administrator has disabled skills in the chat.")}</Notice>}
      {data.settings.enabled && <Notice tone="muted">{data.settings.auto_install ? L("El asistente puede buscar e instalar skills por sí mismo cuando una tarea lo necesite (aparecerán aquí con la etiqueta «instalada por el asistente»).", "The assistant may find and install skills by itself when a task needs one (they show here as “installed by the assistant”).") : L("El asistente puede proponerte skills, pero las instalas tú desde esta página.", "The assistant can suggest skills, but you install them from this page.")}</Notice>}
      {data.skills.length === 0 ? (
        <Card><CardBody><StepsEmpty icon={<Puzzle className="h-5 w-5" />} title={L("Aún no tienes skills", "No skills yet")} steps={[
          L("Abre la pestaña «Catálogo» e instala una (p. ej. doc-coauthoring para escribir documentos con el asistente).", "Open the “Catalog” tab and install one (e.g. doc-coauthoring to write documents with the assistant)."),
          L("O pega un enlace de GitHub en «Instalar desde enlace».", "Or paste a GitHub link in “Install from link”."),
          L("O simplemente pide en el chat «busca una skill para…»: el asistente la encontrará.", "Or just ask in the chat “find a skill for…”: the assistant will find it.")]} /></CardBody></Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {data.skills.map((s: any) => (
            <Card key={s.id} className="p-4">
              <div className="flex items-start gap-2">
                <Puzzle className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
                <div className="min-w-0 flex-1">
                  <button className="font-medium hover:text-accent" onClick={() => setView(s.id)}>{s.name}</button>
                  <div className="mt-0.5 flex flex-wrap gap-1">
                    {s.installed_by === "assistant" && <Badge tone="info">{L("instalada por el asistente", "installed by the assistant")}</Badge>}
                    {s.visibility === "public" && <Badge tone="accent"><Globe className="h-3 w-3" />{L("para todos", "for everyone")}</Badge>}
                    {!s.mine && <Badge>{L("compartida", "shared")}</Badge>}
                    {s.files.length > 0 && <Badge>{s.files.length} {L("archivos", "files")}</Badge>}
                    {s.use_count > 0 && <Badge>{L(`usada ${s.use_count}×`, `used ${s.use_count}×`)}{s.last_used_at ? ` · ${ago(s.last_used_at, lang)}` : ""}</Badge>}
                  </div>
                  <p className="mt-1.5 line-clamp-3 text-xs text-muted">{s.description}</p>
                </div>
                {s.mine && <Switch checked={s.enabled} onChange={(v) => patch(s.id, { enabled: v })} />}
              </div>
              {s.mine && (
                <div className="mt-3 flex flex-wrap gap-1">
                  {s.source_url && <Button size="sm" variant="ghost" onClick={async () => { try { await api(`/api/v1/skills/${s.id}/update`, { method: "POST" }); push("ok", L("Actualizada", "Updated")); mutate(); } catch (e: any) { push("error", e.message); } }}><RefreshCw className="h-3.5 w-3.5" />{L("Actualizar", "Update")}</Button>}
                  {isManager(me) && <Button size="sm" variant="ghost" onClick={() => patch(s.id, { visibility: s.visibility === "public" ? "private" : "public" })}>{s.visibility === "public" ? <><Lock className="h-3.5 w-3.5" />{L("Hacer privada", "Make private")}</> : <><Globe className="h-3.5 w-3.5" />{L("Publicar para todos", "Publish for everyone")}</>}</Button>}
                  <Button size="sm" variant="ghost" onClick={async () => { await api(`/api/v1/skills/${s.id}`, { method: "DELETE" }); mutate(); }}><Trash2 className="h-3.5 w-3.5" />{L("Borrar", "Delete")}</Button>
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
      {view && <SkillView id={view} onClose={() => setView(null)} />}
    </div>
  );
}

function Catalog() {
  const L = useL();
  const { me } = useMe();
  const { push } = useToast();
  const [refresh, setRefresh] = useState(0);
  const { data, mutate, isLoading } = useSWR(`/api/v1/skills/catalog${refresh ? "?refresh=true" : ""}`);
  const [busy, setBusy] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const items = (data ?? []).filter((c: any) => !q || `${c.name} ${c.description}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Input className="max-w-xs" placeholder={L("Filtrar…", "Filter…")} value={q} onChange={(e) => setQ(e.target.value)} />
        {isManager(me) && <Button variant="ghost" size="sm" onClick={() => setRefresh((x) => x + 1)}><RefreshCw className="h-3.5 w-3.5" />{L("Recargar catálogo", "Reload catalog")}</Button>}
      </div>
      {isLoading && <Skeleton className="h-40" />}
      {(data ?? []).filter((c: any) => c.error).map((c: any) => <Notice key={c.repo} tone="warn">{c.repo}: {c.error}</Notice>)}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {items.filter((c: any) => !c.error).map((c: any) => (
          <Card key={c.source} className="flex flex-col p-4">
            <div className="flex items-center gap-2"><Puzzle className="h-4 w-4 text-accent" /><span className="font-medium">{c.name}</span>{c.installed && <Badge tone="ok">{L("instalada", "installed")}</Badge>}</div>
            <p className="mt-1.5 line-clamp-4 flex-1 text-xs text-muted">{c.description}</p>
            <div className="mt-3 flex items-center gap-2">
              <a href={`https://github.com/${c.source.split("/").slice(0, 2).join("/")}/tree/HEAD/${c.source.split("/").slice(2).join("/")}`} target="_blank" rel="noreferrer noopener" className="truncate text-[11px] text-subtle hover:text-accent">{c.repo}</a>
              <Button size="sm" variant={c.installed ? "ghost" : "primary"} className="ml-auto" loading={busy === c.source} onClick={async () => {
                setBusy(c.source);
                try { await api("/api/v1/skills/install", { method: "POST", json: { source: c.source } }); push("ok", L(`«${c.name}» instalada`, `“${c.name}” installed`)); mutate(); } catch (e: any) { push("error", e.message); } finally { setBusy(null); }
              }}><Download className="h-3.5 w-3.5" />{c.installed ? L("Reinstalar", "Reinstall") : L("Instalar", "Install")}</Button>
            </div>
          </Card>
        ))}
      </div>
      <p className="text-[11px] text-subtle">{L("Fuentes del catálogo (las configura el administrador en Ajustes):", "Catalog sources (set by the administrator in Settings):")} {Array.from(new Set((data ?? []).map((c: any) => c.repo))).join(", ")}</p>
    </div>
  );
}

function InstallLink() {
  const L = useL();
  const { push } = useToast();
  const [src, setSrc] = useState("");
  const [busy, setBusy] = useState(false);
  const [own, setOwn] = useState({ name: "", content: "" });
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card>
        <CardHeader title={L("Instalar desde un enlace", "Install from a link")} subtitle={L("GitHub o un SKILL.md público", "GitHub or a public SKILL.md")} />
        <CardBody className="space-y-3">
          <ol className="list-decimal space-y-1 pl-5 text-xs text-muted">
            <li>{L("Busca una skill en GitHub (carpeta con un archivo SKILL.md).", "Find a skill on GitHub (a folder with a SKILL.md file).")}</li>
            <li>{L("Copia la URL de la carpeta (…/tree/main/skills/nombre) o del SKILL.md, o escribe dueño/repo/ruta.", "Copy the folder URL (…/tree/main/skills/name) or the SKILL.md URL, or type owner/repo/path.")}</li>
            <li>{L("Pégala aquí y pulsa Instalar. Se descargan el SKILL.md y sus archivos de texto (máx. 40).", "Paste it here and click Install. SKILL.md and its text files (max 40) are downloaded.")}</li>
          </ol>
          <form className="flex gap-2" onSubmit={async (e) => {
            e.preventDefault(); setBusy(true);
            try { const r = await api("/api/v1/skills/install", { method: "POST", json: { source: src } }); push("ok", L(`«${r.name}» instalada`, `“${r.name}” installed`)); setSrc(""); } catch (er: any) { push("error", er.message); } finally { setBusy(false); }
          }}>
            <Input value={src} onChange={(e) => setSrc(e.target.value)} placeholder="https://github.com/anthropics/skills/tree/main/skills/doc-coauthoring" />
            <Button type="submit" variant="primary" loading={busy} disabled={src.trim().length < 3}><Download className="h-4 w-4" />{L("Instalar", "Install")}</Button>
          </form>
          <Notice tone="muted">{L("Revisa las skills de fuentes que no conozcas: el asistente seguirá sus instrucciones.", "Review skills from sources you do not know: the assistant will follow their instructions.")}</Notice>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title={L("Crear una skill propia", "Create your own skill")} subtitle={L("Tus procedimientos, plantillas o criterios", "Your procedures, templates or criteria")} />
        <CardBody className="space-y-3">
          <Field label={L("Nombre", "Name")} hint={L("minúsculas y guiones, p. ej. informe-ensayos", "lower case and dashes, e.g. test-report")}><Input value={own.name} onChange={(e) => setOwn({ ...own, name: e.target.value })} /></Field>
          <Field label="SKILL.md" hint={L("Primera línea: de qué trata y cuándo usarla. Después, las instrucciones paso a paso.", "First line: what it is and when to use it. Then the step-by-step instructions.")}>
            <Textarea rows={8} value={own.content} onChange={(e) => setOwn({ ...own, content: e.target.value })} placeholder={L("---\nname: informe-ensayos\ndescription: Úsala para redactar informes de ensayos de materiales del laboratorio.\n---\n# Informe de ensayos\n1. Pide norma, probetas y resultados…", "---\nname: test-report\ndescription: Use it to write lab material test reports.\n---\n# Test report\n1. Ask for standard, specimens and results…")} /></Field>
          <div className="flex justify-end"><Button variant="primary" disabled={!own.name || !own.content} onClick={async () => {
            try { await api("/api/v1/skills", { method: "POST", json: own }); push("ok", L("Skill creada", "Skill created")); setOwn({ name: "", content: "" }); } catch (e: any) { push("error", e.message); }
          }}><Plus className="h-4 w-4" />{L("Crear", "Create")}</Button></div>
        </CardBody>
      </Card>
    </div>
  );
}

export default function SkillsPage() {
  const L = useL();
  return (
    <div>
      <PageHeader title="Skills" subtitle={L("Instrucciones expertas que el asistente carga cuando una tarea las necesita", "Expert instructions the assistant loads when a task needs them")} icon={<Puzzle className="h-5 w-5" />} />
      <Guide id="skills" />
      <Tabs tabs={[{ id: "mine", label: L("Mis skills", "My skills"), content: <Installed /> }, { id: "catalog", label: L("Catálogo", "Catalog"), content: <Catalog /> },
        { id: "link", label: L("Instalar desde enlace / crear", "Install from link / create"), content: <InstallLink /> }]} />
    </div>
  );
}
