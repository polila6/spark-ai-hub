"use client";
import { CheckCircle2 } from "lucide-react";
import { use, useEffect, useState } from "react";
import { Button, Card, ErrorBox } from "@/components/ui";
import { api } from "@/lib/api";
import { useL } from "@/lib/i18n";

export default function VerifyPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const L = useL();
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<any>(null);
  useEffect(() => { api("/api/v1/auth/verify-email", { method: "POST", json: { token } }).then(setRes).catch(setErr); }, [token]);
  return (
    <div className="grid min-h-screen place-items-center px-4">
      <Card className="w-full max-w-sm space-y-3 p-6 text-center">
        {err && <ErrorBox error={err} />}
        {res && <>
          <CheckCircle2 className="mx-auto h-10 w-10 text-ok" />
          <p className="font-medium">{L("Correo confirmado", "E-mail confirmed")}</p>
          <p className="text-sm text-muted">{res.pending ? L("Tu solicitud sigue pendiente de aprobación; te avisaremos por correo.", "Your request is still awaiting approval; we will e-mail you.") : L("Ya puedes iniciar sesión.", "You can sign in now.")}</p>
          <a href="/login"><Button variant="primary">{L("Iniciar sesión", "Sign in")}</Button></a>
        </>}
      </Card>
    </div>
  );
}
