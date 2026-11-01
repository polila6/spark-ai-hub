"use client";
import { LifeBuoy, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { AndroidAppCard } from "@/components/android";
import { GuideBody } from "@/components/guide";
import { Card, CardBody, CardHeader, Input, PageHeader } from "@/components/ui";
import { GUIDES, guideText } from "@/lib/guides";
import { useI18n, useL } from "@/lib/i18n";
import { isManager, useMe } from "@/lib/providers";

const ORDER = ["welcome", "android", "chat", "projects", "files", "knowledge", "memory", "connect", "api", "discover", "requests", "library", "compare", "benchmarks", "image", "video", "audio", "science", "jobs", "storage", "monitoring", "settings", "privacy", "users", "access"];

export default function HelpPage() {
  const L = useL();
  const { lang } = useI18n();
  const { me } = useMe();
  const [q, setQ] = useState("");
  const list = useMemo(() => ORDER.filter((id) => {
    const g = GUIDES[id];
    if (!g) return false;
    if (g.roles && !(g.roles.includes(me?.role ?? "") || (g.roles.includes("manager") && isManager(me)))) return false;
    return !q || guideText(g, lang).includes(q.toLowerCase());
  }), [q, lang, me]);
  return (
    <div>
      <PageHeader title={L("Ayuda", "Help")} subtitle={L("Todas las guías paso a paso de la plataforma", "Every step-by-step guide of the platform")} icon={<LifeBuoy className="h-5 w-5" />} />
      <div className="relative mb-4 max-w-lg"><Search className="absolute left-3 top-2.5 h-4 w-4 text-subtle" /><Input className="pl-9" placeholder={L("Buscar en la ayuda (p. ej. «imagen», «clave», «Excel»)", "Search help (e.g. “image”, “key”, “Excel”)")} value={q} onChange={(e) => setQ(e.target.value)} /></div>
      <div className="mb-4 flex flex-wrap gap-2">{list.map((id) => <a key={id} href={`#${id}`} className="rounded-full border border-border px-3 py-1 text-xs hover:bg-elevated">{lang === "en" ? GUIDES[id].title.en : GUIDES[id].title.es}</a>)}</div>
      <div className="grid gap-4 xl:grid-cols-2">
        {list.map((id) => (
          <Card key={id} id={id} className="scroll-mt-20"><CardHeader title={lang === "en" ? GUIDES[id].title.en : GUIDES[id].title.es} /><CardBody><GuideBody g={GUIDES[id]} /></CardBody></Card>
        ))}
      </div>
      <div className="mt-4"><AndroidAppCard /></div>
      <Card className="mt-4"><CardHeader title={L("¿Algo no funciona?", "Something not working?")} /><CardBody className="space-y-2 text-sm text-muted">
        <p>{L("• No carga la web: comprueba que Tailscale está conectado (icono en la barra de tareas) y abre la dirección completa https://<tu-spark>.<tu-tailnet>.ts.net.", "• The site does not load: check Tailscale is connected (taskbar icon) and open the full address https://<tu-spark>.<tu-tailnet>.ts.net.")}</p>
        <p>{L("• El chat tarda en responder la primera vez: el modelo se está cargando en memoria (hasta ~6 min para los grandes).", "• The chat is slow the first time: the model is being loaded into memory (up to ~6 min for large ones).")}</p>
        <p>{L("• Error 401 en una herramienta: la clave es incorrecta, caducó o se revocó; crea otra en Centro API.", "• 401 error in a tool: the key is wrong, expired or revoked; create another in API centre.")}</p>
        <p>{L("• Error 503 / «memoria insuficiente»: hay modelos grandes en uso; espera unos minutos o usa un modelo más pequeño.", "• 503 / “insufficient memory”: large models are in use; wait a few minutes or use a smaller model.")}</p>
        <p>{L("• Para cualquier otra cosa, contacta con el administrador de la plataforma.", "• For anything else, contact the platform administrator.")}</p>
      </CardBody></Card>
    </div>
  );
}
