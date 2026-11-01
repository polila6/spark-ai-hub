"use client";
import * as Tooltip from "@radix-ui/react-tooltip";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import useSWR, { SWRConfig } from "swr";
import { api, fetcher } from "./api";
import { I18nProvider, useI18n } from "./i18n";

// ---------------------------------------------------------------- theme
type Theme = "light" | "dark" | "system";
const ThemeCtx = createContext<{ theme: Theme; setTheme: (t: Theme) => void }>({ theme: "system", setTheme: () => {} });

function applyTheme(t: Theme) {
  const dark = t === "dark" || (t === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("system");
  useEffect(() => {
    let saved: Theme = "system";
    try {
      saved = (localStorage.getItem("sah-theme") as Theme) || "system";
    } catch {
      /* ignore */
    }
    setThemeState(saved);
    applyTheme(saved);
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = () => applyTheme((localStorage.getItem("sah-theme") as Theme) || "system");
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    try {
      localStorage.setItem("sah-theme", t);
    } catch {
      /* ignore */
    }
    applyTheme(t);
  }, []);
  return <ThemeCtx.Provider value={{ theme, setTheme }}>{children}</ThemeCtx.Provider>;
}
export const useTheme = () => useContext(ThemeCtx);

// ---------------------------------------------------------------- toasts
type Toast = { id: number; kind: "ok" | "error" | "info"; text: string };
const ToastCtx = createContext<{ push: (kind: Toast["kind"], text: string) => void }>({ push: () => {} });

function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, kind, text }]);
    setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), kind === "error" ? 8000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={{ push }}>
      {children}
      <div className="fixed bottom-4 right-4 z-[100] flex w-[min(420px,calc(100vw-2rem))] flex-col gap-2" role="status" aria-live="polite">
        {items.map((t) => (
          <div
            key={t.id}
            className={`animate-fade-in rounded-xl border px-4 py-3 text-sm shadow-card backdrop-blur ${
              t.kind === "error" ? "border-danger/40 bg-danger/10 text-danger" : t.kind === "ok" ? "border-ok/40 bg-ok/10 text-ok" : "border-border bg-elevated text-fg"
            }`}
          >
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

// ---------------------------------------------------------------- session
export type Me = { id: string; username: string; display_name: string; role: "admin" | "manager" | "researcher" | "viewer"; preferences: any; email?: string; email_verified?: boolean };
const MeCtx = createContext<{ me: Me | null; loading: boolean; needsTerms: boolean; refresh: () => void; setPrefs: (p: any) => Promise<void> }>({
  me: null, loading: true, needsTerms: false, refresh: () => {}, setPrefs: async () => {},
});

/** Admins and delegated managers (users, sign-ups, model requests). */
export const isManager = (me: Me | null) => me?.role === "admin" || me?.role === "manager";

function MeProvider({ children }: { children: React.ReactNode }) {
  const { data, isLoading, mutate } = useSWR("/api/v1/auth/me", fetcher, { shouldRetryOnError: false, revalidateOnFocus: false });
  const { setLang } = useI18n();
  const me = data?.user ?? null;
  useEffect(() => {
    const l = me?.preferences?.language;
    if (l === "es" || l === "en") setLang(l);
  }, [me?.preferences?.language, setLang]);
  const setPrefs = useCallback(async (p: any) => {
    await api("/api/v1/auth/me/preferences", { method: "PATCH", json: p });
    await mutate();
  }, [mutate]);
  return <MeCtx.Provider value={{ me, loading: isLoading, needsTerms: !!data?.needs_terms, refresh: () => mutate(), setPrefs }}>{children}</MeCtx.Provider>;
}
export const useMe = () => useContext(MeCtx);

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SWRConfig value={{ fetcher, revalidateOnFocus: true, errorRetryCount: 2 }}>
      <I18nProvider>
        <ThemeProvider>
          <ToastProvider>
            <Tooltip.Provider delayDuration={250}>
              <MeProvider>{children}</MeProvider>
            </Tooltip.Provider>
          </ToastProvider>
        </ThemeProvider>
      </I18nProvider>
    </SWRConfig>
  );
}
