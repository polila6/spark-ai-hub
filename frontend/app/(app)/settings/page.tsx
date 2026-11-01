"use client";
import { Archive, Download, KeyRound, Mail, Network, Settings, ShieldAlert, Stethoscope, UserCheck } from "lucide-react";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { Guide } from "@/components/guide";
import { Badge, Button, Card, CardBody, CardHeader, Code, ErrorBox, Field, Input, Modal, Notice, PageHeader, Select, StatusBadge, Switch, Table, Tabs, Td, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { ago, bytes, dt } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useMe, useTheme, useToast } from "@/lib/providers";

function Preferences() {
  const { t, lang, setLang } = useI18n();
  const L = useL();
  const { theme, setTheme } = useTheme();
  const { me, setPrefs } = useMe();
  const { push } = useToast();
  const { data: sessions } = useSWR("/api/v1/auth/sessions");
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const [err, setErr] = useState<any>(null);
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card><CardHeader title={t("settings.preferences")} /><CardBody className="space-y-3">
        <Field label={t("settings.language")}><Select value={lang} onChange={(e) => { const l = e.target.value as any; setLang(l); setPrefs({ language: l }); }}><option value="es">Español</option><option value="en">English</option></Select></Field>
        <Field label={t("settings.theme")}><Select value={theme} onChange={(e) => setTheme(e.target.value as any)}><option value="system">Sistema</option><option value="light">Claro</option><option value="dark">Oscuro</option></Select></Field>
        <Switch checked={me?.preferences?.email_notifications ?? true} onChange={(v) => setPrefs({ email_notifications: v })} label={L("Recibir avisos por correo (modelos listos, solicitudes, exportaciones)", "E-mail notices (models ready, requests, exports)")} />
        <p className="text-xs text-muted">{L("Correo de la cuenta", "Account e-mail")}: {me?.email ?? "—"}</p>
      </CardBody></Card>
      <Card><CardHeader title={t("auth.change_password")} icon={<KeyRound className="h-4 w-4" />} /><CardBody className="space-y-3">
        <Field label={t("auth.current_password")}><Input type="password" autoComplete="current-password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} /></Field>
        <Field label={t("auth.new_password")} hint={t("auth.password_rules")}><Input type="password" autoComplete="new-password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} /></Field>
        {err && <ErrorBox error={err} />}
        <Button variant="primary" onClick={async () => { setErr(null); try { await api("/api/v1/auth/me/password", { method: "POST", json: pw }); push("ok", t("common.save")); setPw({ current_password: "", new_password: "" }); } catch (e) { setErr(e); } }}>{t("common.save")}</Button>
      </CardBody></Card>
      <Card className="xl:col-span-2"><CardHeader title="Sesiones activas" /><Table head={["creada", "última actividad", "IP", "navegador", ""]}>
        {sessions?.map((s: any) => <tr key={s.id}><Td className="text-xs">{dt(s.created_at, lang)}</Td><Td className="text-xs">{ago(s.last_seen_at, lang)}</Td><Td className="text-xs">{s.ip}</Td><Td className="max-w-xs truncate text-xs text-muted">{s.user_agent}</Td><Td>{s.current && <Badge tone="ok">actual</Badge>}</Td></tr>)}
      </Table></Card>
    </div>
  );
}

function Privacy() {
  const L = useL();
  const { push } = useToast();
  const { data } = useSWR("/api/v1/account/summary");
  const [del, setDel] = useState(false);
  const [f, setF] = useState({ password: "", confirm: "" });
  const [err, setErr] = useState<any>(null);
  return (
    <div className="space-y-4">
      <Guide id="privacy" />
      <Card><CardHeader title={L("Lo que guardamos de ti", "What we store about you")} /><CardBody>
        {data && <div className="grid gap-3 text-sm sm:grid-cols-4">
          {[["conversations", L("Chats", "Chats")], ["files", L("Archivos", "Files")], ["memories", L("Memorias", "Memories")], ["projects", L("Proyectos", "Projects")], ["collections", L("Colecciones", "Collections")], ["datasets", "Datasets"], ["artifacts", L("Resultados generados", "Generated results")], ["api_keys", L("Claves API", "API keys")]].map(([k, l]) => <div key={k} className="rounded-xl bg-elevated/60 p-3"><p className="text-xs text-muted">{l}</p><p className="text-lg font-semibold">{data[k]}</p></div>)}
          <div className="rounded-xl bg-elevated/60 p-3 sm:col-span-4"><p className="text-xs text-muted">{L("Espacio usado", "Space used")}</p><p className="font-semibold">{bytes(data.used_bytes)} / {data.quota_gb} GB</p></div>
        </div>}
      </CardBody></Card>
      <Card><CardHeader title={L("Exportar mis datos", "Export my data")} subtitle={L("ZIP con chats (JSON + Markdown), memoria, proyectos, archivos y resultados", "ZIP with chats (JSON + Markdown), memory, projects, files and results")} icon={<Download className="h-4 w-4" />}
        actions={<Button variant="primary" onClick={async () => { await api("/api/v1/account/export", { method: "POST" }); push("ok", L("Preparando la exportación: te avisaremos (🔔) cuando esté lista.", "Preparing the export: we will notify you (🔔) when ready.")); }}>{L("Exportar", "Export")}</Button>} /></Card>
      <Card><CardHeader title={L("Eliminar mi cuenta", "Delete my account")} subtitle={L("Borra de forma permanente todo tu contenido y desactiva la cuenta", "Permanently deletes all your content and deactivates the account")} icon={<ShieldAlert className="h-4 w-4" />}
        actions={<Button variant="danger" onClick={() => setDel(true)}>{L("Eliminar cuenta", "Delete account")}</Button>} /></Card>
      <Modal open={del} onOpenChange={setDel} title={L("Eliminar cuenta", "Delete account")} description={L("No se puede deshacer. Exporta antes tus datos si los quieres conservar.", "This cannot be undone. Export your data first if you want to keep it.")}>
        <div className="space-y-3">
          <Field label={L("Contraseña", "Password")}><Input type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>
          <Field label={L("Escribe ELIMINAR para confirmar", "Type DELETE to confirm")}><Input value={f.confirm} onChange={(e) => setF({ ...f, confirm: e.target.value })} /></Field>
          {err && <ErrorBox error={err} />}
          <div className="flex justify-end"><Button variant="danger" disabled={!["ELIMINAR", "DELETE"].includes(f.confirm) || !f.password} onClick={async () => { setErr(null); try { await api("/api/v1/account", { method: "DELETE", json: f }); window.location.href = "/login"; } catch (e) { setErr(e); } }}>{L("Eliminar definitivamente", "Delete permanently")}</Button></div>
        </div>
      </Modal>
    </div>
  );
}

function Access() {
  const L = useL();
  const { lang } = useI18n();
  const { push } = useToast();
  const { data, mutate } = useSWR("/api/v1/admin/settings");
  const { data: outbox, mutate: mutOut } = useSWR("/api/v1/admin/mail/outbox?limit=20", { refreshInterval: 15000 });
  const [s, setS] = useState<any>(null);
  const [secret, setSecret] = useState({ smtp_password: "", tailscale_api_key: "" });
  const [testTo, setTestTo] = useState("");
  const [err, setErr] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data) { setS(data); setTestTo((x) => x || data.smtp_from || ""); } }, [data]);
  if (!s) return null;
  const set = (k: string, v: any) => setS({ ...s, [k]: v });
  const KEYS = ["signup_mode", "signup_allowed_domains", "terms_text", "terms_version", "tailscale_share_url", "tailscale_device_id", "mail_enabled", "smtp_host", "smtp_port", "smtp_security", "smtp_username", "smtp_from", "smtp_from_name"];
  async function save(extra: any = {}) {
    setErr(null);
    const body: any = { ...extra };
    for (const k of KEYS) if (JSON.stringify(s[k]) !== JSON.stringify(data[k])) body[k] = s[k];
    for (const [k, v] of Object.entries(secret)) if (v) body[k] = v;
    try { await api("/api/v1/admin/settings", { method: "PUT", json: body }); setSecret({ smtp_password: "", tailscale_api_key: "" }); push("ok", L("Guardado", "Saved")); mutate(); } catch (e) { setErr(e); }
  }
  return (
    <div className="space-y-4">
      <Guide id="access" />
      <Card><CardHeader title={L("Correo saliente (SMTP)", "Outgoing mail (SMTP)")} subtitle={L("Para invitaciones, avisos y recuperación de contraseña. Ejemplo: smtp.tu-organizacion.es · 587 · STARTTLS · tu usuario de correo.", "For invitations, notices and password recovery. Example: smtp.your-org.com · 587 · STARTTLS · your mail username.")} icon={<Mail className="h-4 w-4" />}
        actions={<Switch checked={!!s.mail_enabled} onChange={(v) => set("mail_enabled", v)} label={s.mail_enabled ? L("Activado", "On") : L("Desactivado", "Off")} />} />
        <CardBody className="grid gap-3 sm:grid-cols-3">
          <Field label={L("Servidor", "Server")}><Input value={s.smtp_host ?? ""} onChange={(e) => set("smtp_host", e.target.value)} /></Field>
          <Field label={L("Puerto", "Port")}><Input type="number" value={s.smtp_port ?? 587} onChange={(e) => set("smtp_port", Number(e.target.value))} /></Field>
          <Field label={L("Seguridad", "Security")}><Select value={s.smtp_security} onChange={(e) => set("smtp_security", e.target.value)}><option value="starttls">STARTTLS (587)</option><option value="ssl">SSL/TLS (465)</option><option value="none">{L("ninguna", "none")}</option></Select></Field>
          <Field label={L("Usuario SMTP", "SMTP username")} hint={L("Normalmente tu usuario de correo", "Usually your mail username")}><Input autoComplete="off" value={s.smtp_username ?? ""} onChange={(e) => set("smtp_username", e.target.value || null)} /></Field>
          <Field label={L("Contraseña SMTP", "SMTP password")} hint={s.smtp_password ? L("guardada (cifrada) · escribe para cambiarla", "stored (encrypted) · type to change") : L("no configurada", "not set")}><Input type="password" autoComplete="new-password" value={secret.smtp_password} onChange={(e) => setSecret({ ...secret, smtp_password: e.target.value })} /></Field>
          <Field label={L("Remitente", "Sender")} hint="p. ej. ia@tu-organizacion.es"><Input type="email" value={s.smtp_from ?? ""} onChange={(e) => set("smtp_from", e.target.value || null)} /></Field>
          <Field label={L("Nombre del remitente", "Sender name")}><Input value={s.smtp_from_name ?? ""} onChange={(e) => set("smtp_from_name", e.target.value)} /></Field>
          <div className="flex items-end gap-2 sm:col-span-2">
            <Field label={L("Enviar correo de prueba a", "Send test e-mail to")}><Input type="email" value={testTo} onChange={(e) => setTestTo(e.target.value)} /></Field>
            <Button loading={busy} onClick={async () => { setBusy(true); setErr(null); try { await save(); await api("/api/v1/admin/mail/test", { method: "POST", json: { to: testTo } }); push("ok", L("Correo de prueba enviado", "Test e-mail sent")); } catch (e) { setErr(e); } finally { setBusy(false); } }}>{L("Guardar y probar", "Save and test")}</Button>
          </div>
        </CardBody>
        {outbox?.length > 0 && <Table head={[L("Fecha", "Date"), L("Tipo", "Kind"), L("Para", "To"), L("Asunto", "Subject"), L("Estado", "Status")]}>
          {outbox.map((o: any) => <tr key={o.id}><Td className="whitespace-nowrap text-xs text-muted">{dt(o.created_at, lang)}</Td><Td className="text-xs">{o.kind}</Td><Td className="text-xs">{o.to}</Td><Td className="max-w-xs truncate text-xs">{o.subject}</Td>
            <Td><Badge tone={o.status === "sent" ? "ok" : o.status === "failed" ? "danger" : o.status === "skipped" ? "muted" : "info"} title={o.last_error ?? undefined}>{o.status}</Badge></Td></tr>)}
        </Table>}
      </Card>
      <Card><CardHeader title={L("Acceso por Tailscale (opción A)", "Access through Tailscale (option A)")} subtitle={L("Cada persona instala Tailscale y acepta una invitación para usar solo este equipo", "Each person installs Tailscale and accepts an invitation to use only this machine")} icon={<Network className="h-4 w-4" />} />
        <CardBody className="grid gap-3 sm:grid-cols-2">
          <Field label={L("Enlace para compartir el Spark", "Spark share link")} hint={L("login.tailscale.com → Machines → (tu Spark) → ⋯ → Share → Copy share link. Se incluye en cada invitación.", "login.tailscale.com → Machines → (your Spark) → ⋯ → Share → Copy share link. Included in every invitation.")}>
            <Input value={s.tailscale_share_url ?? ""} onChange={(e) => set("tailscale_share_url", e.target.value || null)} placeholder="https://login.tailscale.com/admin/invite/…" /></Field>
          <div />
          <Field label={L("API key de Tailscale (opcional)", "Tailscale API key (optional)")} hint={s.tailscale_api_key ? L("guardada (cifrada)", "stored (encrypted)") : L("Settings → Keys → Generate API key. Crea una invitación de Tailscale por email automáticamente.", "Settings → Keys → Generate API key. Creates a Tailscale invitation per e-mail automatically.")}>
            <Input type="password" autoComplete="new-password" placeholder="tskey-api-…" value={secret.tailscale_api_key} onChange={(e) => setSecret({ ...secret, tailscale_api_key: e.target.value })} /></Field>
          <Field label={L("ID del equipo (node ID)", "Device ID (node ID)")} hint={L("Machines → (tu Spark) → ID (p. ej. n5GjkYJiQp11CNTRL)", "Machines → (your Spark) → ID (e.g. n5GjkYJiQp11CNTRL)")}><Input value={s.tailscale_device_id ?? ""} onChange={(e) => set("tailscale_device_id", e.target.value || null)} /></Field>
          <div className="sm:col-span-2"><Button size="sm" onClick={async () => { setErr(null); try { await save(); const r = await api("/api/v1/admin/tailscale/check", { method: "POST" }); push("ok", `Tailscale OK: ${r.device.name}`); } catch (e) { setErr(e); } }}>{L("Guardar y comprobar API", "Save and check API")}</Button></div>
          <div className="sm:col-span-2"><p className="mb-1 text-xs text-muted">{L("Recomendado: limita a los invitados al puerto 443 del Spark en Tailscale → Access controls:", "Recommended: restrict shared users to port 443 of the Spark in Tailscale → Access controls:")}</p>
            <Code>{`"hosts": { "spark": "100.x.y.z" },   // tailscale ip -4 en el Spark
"grants": [
  { "src": ["autogroup:member"], "dst": ["*"], "ip": ["*"] },
  { "src": ["autogroup:shared"], "dst": ["spark"], "ip": ["tcp:443"] }
]`}</Code></div>
        </CardBody>
      </Card>
      <Card><CardHeader title={L("Registro de cuentas y normas", "Sign-up and terms")} icon={<UserCheck className="h-4 w-4" />} />
        <CardBody className="grid gap-3 sm:grid-cols-2">
          <Field label={L("Modo de registro", "Sign-up mode")}><Select value={s.signup_mode} onChange={(e) => set("signup_mode", e.target.value)}>
            <option value="approval">{L("Con aprobación (recomendado)", "With approval (recommended)")}</option><option value="open">{L("Abierto (activa al momento)", "Open (active immediately)")}</option><option value="closed">{L("Cerrado (solo invitaciones)", "Closed (invitations only)")}</option></Select></Field>
          <Field label={L("Dominios de correo permitidos", "Allowed e-mail domains")} hint={L("Separados por comas. Vacío = cualquiera.", "Comma separated. Empty = any.")}>
            <Input value={(s.signup_allowed_domains ?? []).join(", ")} onChange={(e) => set("signup_allowed_domains", e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} placeholder="example.org, students.example.org" /></Field>
          <Field label={L("Normas de uso (Markdown, vacío = texto por defecto)", "Terms of use (Markdown, empty = default text)")}><Textarea rows={6} value={s.terms_text ?? ""} onChange={(e) => set("terms_text", e.target.value || null)} /></Field>
          <Field label={L("Versión de las normas", "Terms version")} hint={L("Súbela para que todos vuelvan a aceptarlas", "Increase it so everyone accepts them again")}><Input type="number" min={1} value={s.terms_version} onChange={(e) => set("terms_version", Number(e.target.value))} /></Field>
        </CardBody>
      </Card>
      {err && <ErrorBox error={err} />}
      <div className="flex justify-end"><Button variant="primary" onClick={() => save()}>{L("Guardar cambios", "Save changes")}</Button></div>
    </div>
  );
}

function System() {
  const { t } = useI18n();
  const L = useL();
  const { push } = useToast();
  const { data, mutate } = useSWR("/api/v1/admin/settings");
  const { data: models } = useSWR("/api/v1/models");
  const { data: hf, mutate: mutHf } = useSWR("/api/v1/admin/hf-token/status");
  const { data: engines, mutate: mutEng } = useSWR("/api/v1/engines", { refreshInterval: 10000 });
  const [s, setS] = useState<any>(null);
  const [token, setToken] = useState("");
  useEffect(() => { if (data) setS(data); }, [data]);
  if (!s) return null;
  const set = (k: string, v: any) => setS({ ...s, [k]: v });
  async function save() {
    const body: any = {};
    for (const k of Object.keys(s)) if (!["hf_token", "smtp_password", "tailscale_api_key"].includes(k) && JSON.stringify(s[k]) !== JSON.stringify(data[k])) body[k] = s[k];
    try { await api("/api/v1/admin/settings", { method: "PUT", json: body }); push("ok", t("common.save")); mutate(); } catch (e: any) { push("error", e.message); }
  }
  const num = (k: string, label: string, tip?: string) => <Field label={label} tip={tip}><Input type="number" value={s[k] ?? ""} onChange={(e) => set(k, e.target.value === "" ? null : Number(e.target.value))} /></Field>;
  return (
    <div className="space-y-4">
      <Card><CardHeader title={t("settings.scheduler")} subtitle="Modo híbrido por defecto: el modelo preferido permanece residente; el resto se descarga tras el tiempo de inactividad." /><CardBody className="grid gap-3 sm:grid-cols-3">
        <Field label="Modo" tip="persistente: nunca descarga · bajo demanda: descarga rápido · híbrido: fija el preferido, descarga el resto"><Select value={s.scheduler_mode} onChange={(e) => set("scheduler_mode", e.target.value)}><option value="hybrid">hybrid</option><option value="on_demand">on_demand</option><option value="persistent">persistent</option></Select></Field>
        {num("model_idle_timeout_min", "Inactividad (min) · híbrido")}
        {num("on_demand_idle_timeout_min", "Inactividad (min) · bajo demanda")}
        <Field label="Modelo residente preferido"><Select value={s.preferred_resident_model ?? ""} onChange={(e) => set("preferred_resident_model", e.target.value || null)}><option value="">—</option>{models?.filter((m: any) => ["ollama", "vllm"].includes(m.backend)).map((m: any) => <option key={m.id}>{m.alias}</option>)}</Select></Field>
        <Field label="Modelo por defecto"><Select value={s.default_model ?? ""} onChange={(e) => set("default_model", e.target.value || null)}><option value="">—</option>{models?.filter((m: any) => m.capabilities?.includes("chat")).map((m: any) => <option key={m.id}>{m.alias}</option>)}</Select></Field>
        {num("system_reserve_gb", "Reserva del sistema (GB)", "Memoria unificada que el planificador nunca asigna (SO, plano de control, caché)")}
        {num("max_concurrent_gpu_jobs", "Trabajos GPU-heavy concurrentes")}
        {num("max_concurrent_video_jobs", "Vídeos concurrentes")}
        {num("api_default_rpm", "API req/min por defecto")}
      </CardBody></Card>
      <Card><CardHeader title={L("Modelos por defecto (chat y herramientas externas)", "Default models (chat and external tools)")} subtitle={L("Claude Code envía nombres «claude-*»: se traducen a estos modelos. También son los preseleccionados en Conectar herramientas.", "Claude Code sends “claude-*” names: they are mapped to these models. They are also preselected in Connect tools.")} /><CardBody className="grid gap-3 sm:grid-cols-4">
        {([["chat_default_model", L("Chat (por defecto)", "Chat (default)")], ["harness_default_model", L("Herramientas: modelo principal", "Tools: main model")], ["harness_small_model", L("Herramientas: modelo rápido", "Tools: fast model")]] as [string, string][]).map(([k, l]) => (
          <Field key={k} label={l}><Select value={s[k] ?? ""} onChange={(e) => set(k, e.target.value || null)}><option value="">—</option>{models?.filter((m: any) => m.capabilities?.includes("chat")).map((m: any) => <option key={m.id}>{m.alias}</option>)}</Select></Field>))}
        <Field label={L("Embeddings (conocimiento)", "Embeddings (knowledge)")}><Select value={s.embedding_model ?? ""} onChange={(e) => set("embedding_model", e.target.value || null)}><option value="">{L("automático", "automatic")}</option>{models?.filter((m: any) => m.capabilities?.includes("embeddings") && !m.capabilities?.includes("chat")).map((m: any) => <option key={m.id}>{m.alias}</option>)}</Select></Field>
        <Field label={L("Imágenes en el chat", "Images in chat")}><Select value={s.chat_image_model ?? "auto"} onChange={(e) => set("chat_image_model", e.target.value)}><option value="auto">{L("automático (Qwen-Image si está)", "automatic (Qwen-Image if present)")}</option><option value="qwen-image">Qwen-Image</option><option value="sd15">SD 1.5 ({L("rápido", "fast")})</option></Select></Field>
      </CardBody></Card>
      <Card><CardHeader title={L("Chat: internet, skills y agentes", "Chat: web, skills and agents")} subtitle={L("Las búsquedas salen a internet a través del buscador privado del Spark (SearXNG); las páginas internas, el tailnet y la red local nunca se abren. Las skills son texto: nunca se ejecuta su código.", "Searches go out through the Spark's private search engine (SearXNG); internal pages, the tailnet and the local network are never fetched. Skills are text: their code is never executed.")} /><CardBody className="space-y-4">
        <div className="flex flex-wrap gap-6">
          <Switch checked={!!s.web_search_enabled} onChange={(v) => set("web_search_enabled", v)} label={L("Búsqueda web en el chat", "Web search in chat")} />
          <Switch checked={!!s.skills_enabled} onChange={(v) => set("skills_enabled", v)} label="Skills" />
          <Switch checked={!!s.skills_auto_install} onChange={(v) => set("skills_auto_install", v)} label={L("El asistente puede instalar skills solo", "The assistant may install skills by itself")} />
          <Switch checked={!!s.agents_enabled} onChange={(v) => set("agents_enabled", v)} label={L("Subagentes", "Sub-agents")} />
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          {num("max_parallel_agents", L("Agentes en paralelo (1–6)", "Parallel agents (1–6)"), L("Cada agente es una petición más al modelo general (admite 8 a la vez)", "Each agent is one more request to the general model (8 at once)"))}
          <div className="sm:col-span-2"><Field label={L("Fuentes del catálogo de skills (GitHub, una por línea)", "Skill catalog sources (GitHub, one per line)")} tip="owner/repo o owner/repo/ruta">
            <Textarea rows={3} value={(s.skill_sources ?? []).join("\n")} onChange={(e) => set("skill_sources", e.target.value.split("\n").map((x) => x.trim()).filter(Boolean))} /></Field></div>
        </div>
      </CardBody></Card>
      <Card><CardHeader title="Almacenamiento, logs y alertas" /><CardBody className="grid gap-3 sm:grid-cols-3">
        {num("storage_min_free_gb", "Espacio libre mínimo (GB)")}{num("storage_warn_percent", "Aviso de disco (%)")}{num("temperature_alert_c", "Alerta de temperatura (°C)")}
        {num("queue_wait_alert_min", "Alerta de espera en cola (min)")}{num("log_retention_days", "Retención de uso API (días)")}{num("metrics_retention_days", "Retención de métricas (días)")}
        {num("job_retention_days", "Retención de logs de trabajos (días)")}
        <div className="flex items-end"><Switch checked={!!s.log_request_content} onChange={(v) => set("log_request_content", v)} label="Registrar contenido de prompts (desaconsejado)" /></div>
      </CardBody></Card>
      <Card><CardHeader title="Funciones opcionales" /><CardBody className="flex flex-wrap gap-6">
        {Object.keys(s.features).map((k) => <Switch key={k} checked={!!s.features[k]} onChange={(v) => set("features", { ...s.features, [k]: v })} label={k} />)}
      </CardBody></Card>
      <div className="flex justify-end"><Button variant="primary" onClick={save}>{t("common.save")}</Button></div>
      <Card><CardHeader title={t("settings.hf")} subtitle="Token para repositorios gated. Se guarda cifrado; nunca se muestra ni se registra." /><CardBody className="space-y-3">
        <p className="text-sm">{hf?.configured ? <>Configurado · {hf.valid ? <Badge tone="ok">válido · {hf.user}</Badge> : <Badge tone="danger">inválido</Badge>}</> : <Badge>no configurado</Badge>}</p>
        <div className="flex gap-2"><Input type="password" placeholder="hf_…" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
          <Button onClick={async () => { try { await api("/api/v1/admin/settings", { method: "PUT", json: { hf_token: token } }); setToken(""); mutHf(); push("ok", "token"); } catch (e: any) { push("error", e.message); } }} disabled={!token}>{t("common.save")}</Button>
          {hf?.configured && <Button variant="ghost" onClick={async () => { await api("/api/v1/admin/settings", { method: "PUT", json: { hf_token: null } }); mutHf(); }}>{t("common.delete")}</Button>}</div>
      </CardBody></Card>
      <Card><CardHeader title={t("dash.engines")} subtitle="ComfyUI y ML worker arrancan bajo demanda; vLLM arranca al cargar un modelo." /><Table head={["motor", t("common.status"), "versión / imagen", ""]}>
        {engines && Object.entries(engines).map(([k, e]: any) => <tr key={k}><Td className="font-medium">{k}</Td><Td><StatusBadge state={e.status} /></Td><Td className="max-w-sm truncate text-xs text-muted">{e.version} {e.extra?.image}</Td>
          <Td><div className="flex gap-1">
            {["comfyui", "mlworker"].includes(k) && e.status === "stopped" && <Button size="sm" onClick={async () => { try { await api(`/api/v1/engines/${k}/start`, { method: "POST" }); mutEng(); } catch (x: any) { push("error", x.message); } }}>Iniciar</Button>}
            {["comfyui", "mlworker", "vllm"].includes(k) && e.status === "ok" && <Button size="sm" variant="ghost" onClick={async () => { try { await api(`/api/v1/engines/${k}/stop`, { method: "POST" }); mutEng(); } catch (x: any) { push("error", x.message); } }}>Detener</Button>}
            {k === "ollama" && <Button size="sm" variant="ghost" onClick={async () => { try { await api(`/api/v1/engines/ollama/restart`, { method: "POST" }); push("ok", "ollama"); } catch (x: any) { push("error", x.message); } }}>Reiniciar</Button>}
          </div></Td></tr>)}
      </Table></Card>
    </div>
  );
}

function Backups() {
  const { t, lang } = useI18n();
  const { push } = useToast();
  const { data, mutate } = useSWR("/api/v1/admin/backups", { refreshInterval: 10000 });
  const { data: s, mutate: mutS } = useSWR("/api/v1/admin/settings");
  const [userData, setUserData] = useState(false);
  return (
    <div className="space-y-4">
      {data?.warning && <Notice tone="warn">{data.warning} {data.external_mounted ? "Destino externo montado en /backups-external." : "No hay destino externo montado (ver docs/BACKUP_RECOVERY.md)."}</Notice>}
      <Card><CardHeader title={t("settings.backups")} icon={<Archive className="h-4 w-4" />} actions={<>
        <Switch checked={userData} onChange={setUserData} label="incluir datos de usuario" />
        <Button variant="primary" onClick={async () => { const r = await api("/api/v1/admin/backups", { method: "POST", json: { include_user_data: userData } }); push("ok", `job ${r.job_id.slice(0, 8)}`); }}>Crear copia ahora</Button></>} />
        {s && <CardBody className="grid gap-3 sm:grid-cols-4">
          <Field label="Programación"><Select value={s.backup_schedule} onChange={async (e) => { await api("/api/v1/admin/settings", { method: "PUT", json: { backup_schedule: e.target.value } }); mutS(); }}><option value="off">off</option><option value="daily">daily</option><option value="weekly">weekly</option></Select></Field>
          <Field label="Hora (UTC)"><Input type="number" min={0} max={23} defaultValue={s.backup_hour} onBlur={async (e) => { await api("/api/v1/admin/settings", { method: "PUT", json: { backup_hour: Number(e.target.value) } }); mutS(); }} /></Field>
          <Field label="Conservar"><Input type="number" min={1} defaultValue={s.backup_keep} onBlur={async (e) => { await api("/api/v1/admin/settings", { method: "PUT", json: { backup_keep: Number(e.target.value) } }); mutS(); }} /></Field>
          <Field label="Subcarpeta externa"><Input defaultValue={s.backup_external_path ?? ""} placeholder="spark-ai-hub" onBlur={async (e) => { await api("/api/v1/admin/settings", { method: "PUT", json: { backup_external_path: e.target.value || null } }); mutS(); }} /></Field>
        </CardBody>}
        <Table head={["copia", "fecha", t("common.size"), "datos usuario", "filas", ""]}>
          {data?.backups?.map((b: any) => <tr key={b.name}><Td className="font-mono text-xs">{b.name}</Td><Td className="text-xs">{b.created_at}</Td><Td>{bytes(b.size_bytes)}</Td><Td>{b.includes_user_data ? "sí" : "no"}</Td><Td className="max-w-xs truncate text-[10px] text-muted">{b.table_counts && Object.entries(b.table_counts).map(([k, v]) => `${k}:${v}`).join(" ")}</Td>
            <Td><Button size="sm" variant="outline" onClick={async () => { const r = await api(`/api/v1/admin/backups/${b.name}/restore-test`, { method: "POST" }); push("ok", `restore test · job ${r.job_id.slice(0, 8)}`); }}>Probar restauración</Button></Td></tr>)}
        </Table>
      </Card>
    </div>
  );
}

function Diagnostics() {
  const { data } = useSWR("/api/v1/admin/diagnostics");
  return (
    <Card><CardHeader title="Diagnóstico" icon={<Stethoscope className="h-4 w-4" />} actions={<a href="/api/v1/admin/diagnostics/bundle"><Button size="sm" variant="primary"><Download className="h-3.5 w-3.5" />Paquete de diagnóstico (secretos redactados)</Button></a>} />
      <CardBody><pre className="max-h-[60vh] overflow-auto rounded-xl bg-elevated p-3 font-mono text-[11px]">{data ? JSON.stringify(data, null, 2) : "…"}</pre></CardBody></Card>
  );
}

export default function SettingsPage() {
  const { t } = useI18n();
  const { me } = useMe();
  const L = useL();
  const tabs = [{ id: "pref", label: t("settings.preferences"), content: <Preferences /> }, { id: "privacy", label: L("Privacidad", "Privacy"), content: <Privacy /> }];
  if (me?.role === "admin") tabs.push({ id: "access", label: L("Acceso y correo", "Access and mail"), content: <Access /> }, { id: "sys", label: t("settings.system"), content: <System /> }, { id: "bak", label: t("settings.backups"), content: <Backups /> }, { id: "diag", label: t("settings.diagnostics"), content: <Diagnostics /> });
  return (
    <div>
      <PageHeader title={t("settings.title")} icon={<Settings className="h-5 w-5" />} />
      <Guide id="settings" />
      <Tabs tabs={tabs} />
    </div>
  );
}
