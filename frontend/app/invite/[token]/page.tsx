"use client";
import { use, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Button, Card, ErrorBox, Field, Input } from "@/components/ui";

export default function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const { t } = useI18n();
  const [info, setInfo] = useState<any>(null);
  const [err, setErr] = useState<any>(null);
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api(`/api/v1/auth/invitation/${token}`).then(setInfo).catch(setErr); }, [token]);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pw !== pw2) return setErr(new Error("≠"));
    setBusy(true);
    try {
      await api("/api/v1/auth/invitation/accept", { method: "POST", json: { token, password: pw, display_name: name || undefined } });
      window.location.href = "/";
    } catch (e) { setErr(e); } finally { setBusy(false); }
  }
  return (
    <div className="grid min-h-screen place-items-center px-4">
      <Card className="w-full max-w-sm p-6">
        <h1 className="text-lg font-semibold">{info?.kind === "reset" ? t("auth.reset_title") : t("auth.invite_title")}</h1>
        {info && <p className="mt-1 text-sm text-muted">{info.username}</p>}
        {err && <div className="mt-4"><ErrorBox error={err} /></div>}
        {info && (
          <form onSubmit={submit} className="mt-5 space-y-4">
            {info.kind !== "reset" && <Field label={t("common.name")}><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>}
            <Field label={t("auth.new_password")} hint={t("auth.password_rules")}><Input type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} required minLength={10} /></Field>
            <Field label={t("auth.new_password")}><Input type="password" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} required /></Field>
            <Button type="submit" variant="primary" className="w-full" loading={busy}>{t("common.confirm")}</Button>
          </form>
        )}
      </Card>
    </div>
  );
}
