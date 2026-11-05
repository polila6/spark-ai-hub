"use client";
// First-run setup, shown once to the administrator of a brand-new hub: the default language and, optionally, the name of
// the organisation or lab. Nothing about any organisation is built in: whatever is typed here (or nothing) is what the
// terms of use, the e-mails and the assistant's prompt mention. Everything can be changed later in Settings → System.
import { ArrowRight, Check, Languages, Building2, Rocket } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { Button, Field, Input, Modal, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { useI18n, useL } from "@/lib/i18n";
import { useMe, useToast } from "@/lib/providers";

export function SetupGate() {
  const { me } = useMe();
  const { data: pub, mutate } = useSWR(me?.role === "admin" ? "/api/v1/settings/public" : null);
  const { lang, setLang } = useI18n();
  const L = useL();
  const { push } = useToast();
  const [step, setStep] = useState(0);
  const [org, setOrg] = useState("");
  const [busy, setBusy] = useState(false);
  if (me?.role !== "admin" || !pub?.needs_setup) return null;

  async function finish(skip: boolean) {
    setBusy(true);
    try {
      await api("/api/v1/admin/setup", { method: "POST", json: skip ? {} : { default_language: lang, org_name: org.trim() } });
      await mutate();
    } catch (e: any) { push("error", e.message); } finally { setBusy(false); }
  }
  const steps = [L("Idioma", "Language"), L("Tu organización", "Your organisation"), L("Listo", "Done")];
  return (
    <Modal open onOpenChange={() => {}} title={L("Configura tu Spark AI Hub", "Set up your Spark AI Hub")}
      description={L("Dos preguntas rápidas. Puedes cambiarlas después en Ajustes → Sistema.", "Two quick questions. You can change them later in Settings → System.")}>
      <ol className="mb-4 flex items-center gap-2 text-xs">
        {steps.map((s, i) => (
          <li key={s} className={cx("flex items-center gap-1.5", i === step ? "font-medium text-fg" : "text-muted")}>
            <span className={cx("grid h-5 w-5 place-items-center rounded-full border text-[10px]", i < step ? "border-accent bg-accent text-accent-fg" : i === step ? "border-accent text-accent" : "border-border")}>{i < step ? <Check className="h-3 w-3" /> : i + 1}</span>{s}{i < steps.length - 1 && <span className="mx-1 text-subtle">›</span>}
          </li>
        ))}
      </ol>

      {step === 0 && <div className="space-y-3">
        <p className="flex items-center gap-2 text-sm font-medium"><Languages className="h-4 w-4 text-accent" />{L("Idioma predeterminado", "Default language")}</p>
        <p className="text-sm text-muted">{L("Es el que verá cada persona la primera vez que entre. Cada usuario podrá elegir el suyo en Ajustes → Preferencias, y esa elección es la que cuenta.",
          "It is what each person sees the first time they sign in. Every user can pick their own in Settings → Preferences, and that choice is the one that counts.")}</p>
        <div className="grid gap-2 sm:grid-cols-2">
          {([["es", "Español", "Menús, avisos y respuestas en español"], ["en", "English", "Menus, notices and answers in English"]] as const).map(([code, name, hint]) => (
            <button key={code} type="button" onClick={() => setLang(code)} aria-pressed={lang === code}
              className={cx("rounded-xl border p-3 text-left transition-colors", lang === code ? "border-accent bg-accent/10" : "border-border hover:bg-elevated")}>
              <span className="block text-sm font-medium">{name}</span><span className="block text-xs text-muted">{hint}</span>
            </button>
          ))}
        </div>
      </div>}

      {step === 1 && <div className="space-y-3">
        <p className="flex items-center gap-2 text-sm font-medium"><Building2 className="h-4 w-4 text-accent" />{L("Nombre de tu organización o laboratorio (opcional)", "Your organisation or lab name (optional)")}</p>
        <p className="text-sm text-muted">{L("Aparece en las normas de uso, en los correos y en la presentación del asistente. Si lo dejas vacío no se menciona ninguna organización.",
          "It appears in the terms of use, in e-mails and in the assistant's introduction. If you leave it empty no organisation is mentioned.")}</p>
        <Field label={L("Nombre", "Name")}><Input value={org} maxLength={120} placeholder={L("p. ej. Laboratorio de Robótica · Universidad de Ejemplo", "e.g. Robotics Lab · Example University")} onChange={(e) => setOrg(e.target.value)} autoFocus /></Field>
      </div>}

      {step === 2 && <div className="space-y-3">
        <p className="flex items-center gap-2 text-sm font-medium"><Rocket className="h-4 w-4 text-accent" />{L("Ya está. Qué hacer ahora", "All set. What to do next")}</p>
        <ol className="space-y-2 text-sm">
          {([["/discover", L("Instala tu primer modelo", "Install your first model"), L("Busca en Hugging Face; el hub te dice si cabe y con qué motor.", "Search Hugging Face; the hub tells you whether it fits and which engine runs it.")],
            ["/users", L("Invita a tu equipo", "Invite your team"), L("Crea cuentas o abre el registro con aprobación.", "Create accounts or open sign-up with approval.")],
            ["/connect", L("Conecta tus herramientas", "Connect your tools"), L("Editores, apps y scripts que usan la API del hub.", "Editors, apps and scripts that use the hub's API.")]] as const).map(([href, title, hint]) => (
            <li key={href}><Link href={href} onClick={() => finish(false)} className="flex items-start gap-2 rounded-lg border border-border p-2.5 hover:bg-elevated">
              <ArrowRight className="mt-0.5 h-4 w-4 shrink-0 text-accent" /><span><span className="block font-medium">{title}</span><span className="block text-xs text-muted">{hint}</span></span></Link></li>
          ))}
        </ol>
      </div>}

      <div className="mt-5 flex items-center justify-between gap-2">
        <Button variant="ghost" disabled={busy} onClick={() => finish(true)}>{L("Saltar por ahora", "Skip for now")}</Button>
        <div className="flex gap-2">
          {step > 0 && <Button variant="outline" disabled={busy} onClick={() => setStep(step - 1)}>{L("Atrás", "Back")}</Button>}
          {step < 2 ? <Button variant="primary" onClick={() => setStep(step + 1)}>{L("Siguiente", "Next")}</Button>
            : <Button variant="primary" loading={busy} onClick={() => finish(false)}>{L("Terminar", "Finish")}</Button>}
        </div>
      </div>
    </Modal>
  );
}
