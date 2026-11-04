"use client";
import * as DM from "@radix-ui/react-dropdown-menu";
import {
  Activity, AlarmClock, AudioLines, Gauge, SquareTerminal, BarChart3, Bell, Bot, Boxes, Brain, Clapperboard, Code2, Cpu, FlaskConical, FolderKanban, FolderOpen,
  GitCompare, HardDrive, Image as ImageIcon, Inbox, LayoutDashboard, LibraryBig, LifeBuoy, ListChecks, Loader2, LogOut, Menu,
  MessagesSquare, Monitor, Moon, Plug, Puzzle, Search, Settings, Sun, Users, X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useI18n, useL } from "@/lib/i18n";
import { isManager, useMe, useTheme, useToast } from "@/lib/providers";
import { MemoryPill } from "./memory";
import { Badge, Button, cx, Modal, Skeleton } from "./ui";

type Item = { href: string; key: string; icon: any; admin?: boolean; label?: [string, string] };
const GROUPS: { key: string; label?: [string, string]; items: Item[] }[] = [
  { key: "", items: [{ href: "/", key: "nav.dashboard", icon: LayoutDashboard }] },
  { key: "space", label: ["Mi espacio", "My space"], items: [
    { href: "/playground", key: "nav.chat", label: ["Chat", "Chat"], icon: MessagesSquare },
    { href: "/projects", key: "nav.projects", label: ["Proyectos", "Projects"], icon: FolderKanban },
    { href: "/files", key: "nav.files", label: ["Mis archivos", "My files"], icon: FolderOpen },
    { href: "/workspace", key: "nav.workspace", label: ["Workspace", "Workspace"], icon: SquareTerminal },
    { href: "/knowledge", key: "nav.knowledge", label: ["Conocimiento", "Knowledge"], icon: LibraryBig },
    { href: "/memory", key: "nav.memory", label: ["Memoria", "Memory"], icon: Brain },
    { href: "/skills", key: "nav.skills", label: ["Skills", "Skills"], icon: Puzzle },
    { href: "/agents", key: "nav.agents", label: ["Agentes", "Agents"], icon: Bot },
    { href: "/tasks", key: "nav.tasks", label: ["Tareas", "Tasks"], icon: AlarmClock },
    { href: "/usage", key: "nav.usage", label: ["Uso", "Usage"], icon: Gauge },
  ] },
  { key: "nav.group.models", items: [
    { href: "/discover", key: "nav.discover", icon: Search },
    { href: "/models", key: "nav.library", icon: Boxes },
    { href: "/requests", key: "nav.requests", label: ["Solicitudes", "Requests"], icon: Inbox },
    { href: "/compare", key: "nav.compare", icon: GitCompare },
    { href: "/benchmarks", key: "nav.benchmarks", icon: BarChart3 },
  ] },
  { key: "nav.group.create", items: [
    { href: "/image", key: "nav.image", icon: ImageIcon },
    { href: "/video", key: "nav.video", icon: Clapperboard },
    { href: "/audio", key: "nav.audio", icon: AudioLines },
  ] },
  { key: "nav.group.research", items: [
    { href: "/science", key: "nav.science", icon: FlaskConical },
    { href: "/jobs", key: "nav.jobs", icon: ListChecks },
    { href: "/robot", key: "nav.robot", label: ["Robot", "Robot"], icon: Bot },
    { href: "/connect", key: "nav.connect", label: ["Conectar herramientas", "Connect tools"], icon: Plug },
    { href: "/api-center", key: "nav.api", icon: Code2 },
  ] },
  { key: "nav.group.platform", items: [
    { href: "/storage", key: "nav.storage", icon: HardDrive },
    { href: "/users", key: "nav.users", icon: Users, admin: true },
    { href: "/monitoring", key: "nav.monitoring", icon: Activity },
    { href: "/settings", key: "nav.settings", icon: Settings },
    { href: "/help", key: "nav.help", label: ["Ayuda", "Help"], icon: LifeBuoy },
  ] },
];

function Nav({ onNavigate }: { onNavigate?: () => void }) {
  const path = usePathname();
  const { t } = useI18n();
  const L = useL();
  const { me } = useMe();
  const { data: jobs } = useSWR("/api/v1/jobs?status=queued,running,preparing&limit=1", { refreshInterval: 10000 });
  const { data: pending } = useSWR(isManager(me) ? "/api/v1/downloads?pending=true" : null, { refreshInterval: 30000 });
  const { data: signups } = useSWR(isManager(me) ? "/api/v1/users?status=pending" : null, { refreshInterval: 30000 });
  return (
    <nav aria-label="Main" className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
      {GROUPS.map((g) => (
        <div key={g.key || "root"}>
          {g.key && <p className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-wider text-subtle">{g.label ? L(...g.label) : t(g.key)}</p>}
          <ul className="space-y-0.5">
            {g.items.filter((i) => !i.admin || isManager(me)).map((i) => {
              const active = i.href === "/" ? path === "/" : path.startsWith(i.href);
              const Icon = i.icon;
              return (
                <li key={i.href}>
                  <Link href={i.href} onClick={onNavigate} aria-current={active ? "page" : undefined}
                    className={cx("group flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition",
                      active ? "bg-accent/12 font-medium text-fg" : "text-muted hover:bg-elevated hover:text-fg")}>
                    <Icon className={cx("h-4 w-4 shrink-0", active ? "text-accent" : "text-subtle group-hover:text-muted")} />
                    <span className="truncate">{i.label ? L(...i.label) : t(i.key)}</span>
                    {i.href === "/jobs" && jobs?.total > 0 && <Badge tone="info" className="ml-auto">{jobs.total}</Badge>}
                    {i.href === "/requests" && pending?.length > 0 && <Badge tone="warn" className="ml-auto">{pending.length}</Badge>}
                    {i.href === "/users" && signups?.length > 0 && <Badge tone="warn" className="ml-auto">{signups.length}</Badge>}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

function Brand() {
  const { t } = useI18n();
  return (
    <Link href="/" className="flex items-center gap-2.5 px-5 py-4">
      <div className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-accent to-emerald-600 text-white shadow-sm"><Cpu className="h-4 w-4" /></div>
      <div className="leading-tight">
        <p className="text-sm font-semibold text-fg">{t("app.name")}</p>
        <p className="text-[10px] text-subtle">{t("app.tagline")}</p>
      </div>
    </Link>
  );
}

function Bell_() {
  const L = useL();
  const router = useRouter();
  const { data, mutate } = useSWR("/api/v1/notifications?limit=30", { refreshInterval: 30000 });
  return (
    <DM.Root onOpenChange={(o) => { if (o) mutate(); }}>
      <DM.Trigger asChild>
        <button aria-label={L("Notificaciones", "Notifications")} className="relative grid h-9 w-9 place-items-center rounded-lg text-muted hover:bg-elevated hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40">
          <Bell className="h-4 w-4" />
          {data?.unread > 0 && <span className="absolute right-1.5 top-1.5 grid h-4 min-w-4 place-items-center rounded-full bg-danger px-1 text-[9px] font-bold text-white">{data.unread > 9 ? "9+" : data.unread}</span>}
        </button>
      </DM.Trigger>
      <DM.Portal>
        <DM.Content align="end" sideOffset={6} className="z-[90] max-h-[70vh] w-[340px] overflow-y-auto rounded-xl border border-border bg-surface p-1 shadow-card">
          <div className="flex items-center justify-between px-3 py-2">
            <span className="text-sm font-semibold">{L("Notificaciones", "Notifications")}</span>
            {data?.unread > 0 && <button className="text-xs text-accent" onClick={async () => { await api("/api/v1/notifications/read-all", { method: "POST" }); mutate(); }}>{L("Marcar todo como leído", "Mark all read")}</button>}
          </div>
          {!data?.items?.length && <p className="px-3 py-6 text-center text-xs text-muted">{L("No tienes notificaciones.", "No notifications.")}</p>}
          {data?.items?.map((n: any) => (
            <DM.Item key={n.id} onSelect={async () => { if (!n.read) await api(`/api/v1/notifications/${n.id}/read`, { method: "POST" }); mutate(); if (n.link) { if (n.link.startsWith("/api/")) window.location.href = n.link; else router.push(n.link); } }}
              className={cx("cursor-pointer rounded-lg px-3 py-2 outline-none data-[highlighted]:bg-elevated", !n.read && "bg-accent/[0.06]")}>
              <p className="flex items-center gap-2 text-sm text-fg">{!n.read && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />}{n.title}</p>
              {n.body && <p className="mt-0.5 line-clamp-2 text-xs text-muted">{n.body}</p>}
              <p className="mt-1 text-[10px] text-subtle">{new Date(n.created_at).toLocaleString()}</p>
            </DM.Item>
          ))}
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  );
}

/** Header indicator for chats still answering in the background; announces when each one finishes. */
function ChatActivity() {
  const L = useL();
  const router = useRouter();
  const path = usePathname();
  const { push } = useToast();
  const { data } = useSWR("/api/v1/playground/active", { refreshInterval: (d: any) => (d?.length ? 3000 : 8000) });
  const prev = useRef<Map<string, any>>(new Map());
  useEffect(() => {
    if (!data) return;
    const now = new Map<string, any>(data.map((t: any) => [t.conversation_id, t]));
    for (const [cid, t] of prev.current) {
      if (!now.has(cid)) {
        const here = t.workspace_id ? path.startsWith("/workspace") : path.startsWith("/playground");
        push("ok", L(`Respuesta lista: «${t.title}»`, `Answer ready: “${t.title}”`) + (here ? "" : t.workspace_id ? L(" · ábrela en el Workspace", " · open it in the Workspace") : L(" · ábrela en el Chat", " · open it in Chat")));
        try {
          if (document.hidden && "Notification" in window && Notification.permission === "granted") new Notification("Spark AI Hub", { body: t.title });
        } catch { /* notifications unavailable */ }
      }
    }
    prev.current = now;
  }, [data, path, push, L]);
  if (!data?.length) return null;
  const since = (iso: string) => { const s = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000)); return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min`; };
  return (
    <DM.Root>
      <DM.Trigger asChild>
        <button className="flex items-center gap-1.5 rounded-full bg-info/12 px-2.5 py-1 text-xs font-medium text-info ring-1 ring-inset ring-info/25">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          <span className="hidden sm:inline">{data.length === 1 ? L("1 chat trabajando", "1 chat working") : L(`${data.length} chats trabajando`, `${data.length} chats working`)}</span>
          <span className="sm:hidden">{data.length}</span>
        </button>
      </DM.Trigger>
      <DM.Portal>
        <DM.Content align="end" sideOffset={6} className="z-[90] w-[340px] rounded-xl border border-border bg-surface p-1 shadow-card">
          <p className="px-2.5 py-1.5 text-[11px] text-muted">{L("Siguen trabajando aunque cambies de página. Ábrelos para ver el progreso o páralos.", "They keep working when you change page. Open them to see progress, or stop them.")}</p>
          {data.map((t: any) => (
            <div key={t.conversation_id} className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm hover:bg-elevated">
              <button className="min-w-0 flex-1 text-left" onClick={() => router.push(t.link ?? `/playground?c=${t.conversation_id}`)}>
                <p className="truncate font-medium">{t.title}</p>
                <p className="text-[11px] text-muted">{t.workspace_id ? "Workspace · " : ""}{L("hace", "")} {since(t.started_at)}{L("", " ago")}{t.last ? ` · ${t.last}` : ""}</p>
              </button>
              <button className="rounded-md px-2 py-1 text-xs text-danger hover:bg-danger/10" title={L("Parar esta respuesta", "Stop this answer")}
                onClick={async () => { await api(`/api/v1/playground/conversations/${t.conversation_id}/cancel`, { method: "POST" }).catch(() => {}); push("info", L("Parando…", "Stopping…")); }}>{L("Parar", "Stop")}</button>
            </div>
          ))}
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  );
}

function TermsGate() {
  const { needsTerms, refresh } = useMe();
  const { lang } = useI18n();
  const L = useL();
  const { data } = useSWR(needsTerms ? "/api/v1/auth/terms" : null);
  const [busy, setBusy] = useState(false);
  if (!needsTerms || !data) return null;
  return (
    <Modal open onOpenChange={() => {}} title={L("Normas de uso", "Terms of use")} description={L("Léelas y acéptalas para continuar.", "Read and accept them to continue.")}>
      <div className="prose-chat max-h-[50vh] overflow-y-auto text-sm"><ReactMarkdown>{data.text[lang] ?? data.text.es}</ReactMarkdown></div>
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={async () => { await api("/api/v1/auth/logout", { method: "POST" }).catch(() => {}); window.location.href = "/login"; }}>{L("Salir", "Sign out")}</Button>
        <Button variant="primary" loading={busy} onClick={async () => { setBusy(true); await api("/api/v1/auth/me/accept-terms", { method: "POST" }); refresh(); setBusy(false); }}>{L("Acepto", "I accept")}</Button>
      </div>
    </Modal>
  );
}

function UserMenu() {
  const { me } = useMe();
  const { t, lang, setLang } = useI18n();
  const { theme, setTheme } = useTheme();
  const { setPrefs } = useMe();
  const router = useRouter();
  if (!me) return <Skeleton className="h-8 w-28" />;
  return (
    <div className="flex items-center gap-1">
      <ChatActivity />
      <MemoryPill />
      <Bell_ />
      <Button variant="ghost" size="sm" aria-label={t("settings.language")} onClick={() => { const l = lang === "es" ? "en" : "es"; setLang(l); setPrefs({ language: l }).catch(() => {}); }}>
        {lang.toUpperCase()}
      </Button>
      <Button variant="ghost" size="icon" aria-label={t("settings.theme")} onClick={() => setTheme(theme === "dark" ? "light" : theme === "light" ? "system" : "dark")}>
        {theme === "dark" ? <Moon className="h-4 w-4" /> : theme === "light" ? <Sun className="h-4 w-4" /> : <Monitor className="h-4 w-4" />}
      </Button>
      <DM.Root>
        <DM.Trigger asChild>
          <button className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40">
            <span className="grid h-7 w-7 place-items-center rounded-full bg-accent/15 text-xs font-semibold text-accent">{me.display_name?.[0]?.toUpperCase() ?? "?"}</span>
            <span className="hidden text-left leading-tight sm:block"><span className="block text-xs font-medium text-fg">{me.display_name}</span><span className="block text-[10px] text-subtle">{me.role}</span></span>
          </button>
        </DM.Trigger>
        <DM.Portal>
          <DM.Content align="end" sideOffset={6} className="z-[90] min-w-[180px] rounded-xl border border-border bg-surface p-1 shadow-card">
            <DM.Item onSelect={() => router.push("/settings")} className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm text-fg outline-none data-[highlighted]:bg-elevated"><Settings className="h-4 w-4" />{t("nav.settings")}</DM.Item>
            <DM.Item onSelect={async () => { await api("/api/v1/auth/logout", { method: "POST" }).catch(() => {}); window.location.href = "/login"; }}
              className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm text-danger outline-none data-[highlighted]:bg-danger/10"><LogOut className="h-4 w-4" />{t("auth.logout")}</DM.Item>
          </DM.Content>
        </DM.Portal>
      </DM.Root>
    </div>
  );
}

/** Bottom tab bar on phones (and in the Android app). Items depend on the role. */
function MobileTabs({ onMore }: { onMore: () => void }) {
  const path = usePathname();
  const L = useL();
  const { me } = useMe();
  const { data: pending } = useSWR(isManager(me) ? "/api/v1/downloads?pending=true" : null, { refreshInterval: 30000 });
  const { data: signups } = useSWR(isManager(me) ? "/api/v1/users?status=pending" : null, { refreshInterval: 30000 });
  const items: { href: string; label: string; icon: any; badge?: number }[] = isManager(me)
    ? [{ href: "/playground", label: L("Chat", "Chat"), icon: MessagesSquare }, { href: "/files", label: L("Archivos", "Files"), icon: FolderOpen },
       { href: "/requests", label: L("Solicitudes", "Requests"), icon: Inbox, badge: pending?.length }, { href: "/users", label: L("Usuarios", "Users"), icon: Users, badge: signups?.length }]
    : [{ href: "/playground", label: L("Chat", "Chat"), icon: MessagesSquare }, { href: "/projects", label: L("Proyectos", "Projects"), icon: FolderKanban },
       { href: "/files", label: L("Archivos", "Files"), icon: FolderOpen }, { href: "/image", label: L("Imágenes", "Images"), icon: ImageIcon }];
  return (
    <nav aria-label="Mobile" className="fixed inset-x-0 bottom-0 z-50 grid grid-cols-5 border-t border-border bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden">
      {items.map((i) => {
        const active = path.startsWith(i.href);
        const Icon = i.icon;
        return (
          <Link key={i.href} href={i.href} className={cx("relative flex flex-col items-center gap-0.5 py-2 text-[10px]", active ? "text-accent" : "text-muted")}>
            <Icon className="h-5 w-5" />{i.label}
            {!!i.badge && <span className="absolute right-[22%] top-1 grid h-4 min-w-4 place-items-center rounded-full bg-danger px-1 text-[9px] font-bold text-white">{i.badge}</span>}
          </Link>
        );
      })}
      <button onClick={onMore} className="flex flex-col items-center gap-0.5 py-2 text-[10px] text-muted"><Menu className="h-5 w-5" />{L("Más", "More")}</button>
    </nav>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { me, loading } = useMe();
  const [open, setOpen] = useState(false);
  const path = usePathname();
  useEffect(() => {
    if (!loading && !me) window.location.href = `/login?next=${encodeURIComponent(path)}`;
  }, [loading, me, path]);
  if (!me) {
    return <div className="grid min-h-screen place-items-center"><Skeleton className="h-10 w-48" /></div>;
  }
  return (
    <div className="flex min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-[200] focus:rounded-lg focus:bg-accent focus:px-3 focus:py-2 focus:text-accent-fg">Skip to content</a>
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col border-r border-border bg-surface/70 backdrop-blur lg:flex">
        <Brand />
        <Nav />
      </aside>
      {open && (
        <div className="fixed inset-0 z-[70] lg:hidden" role="dialog" aria-modal="true">
          <div className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <aside className="absolute left-0 top-0 flex h-full w-72 flex-col border-r border-border bg-surface shadow-2xl">
            <div className="flex items-center justify-between pr-3"><Brand /><Button variant="ghost" size="icon" aria-label="Close menu" onClick={() => setOpen(false)}><X className="h-4 w-4" /></Button></div>
            <Nav onNavigate={() => setOpen(false)} />
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-40 flex h-14 items-center justify-between gap-3 border-b border-border bg-bg/80 px-4 backdrop-blur sm:px-6">
          <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open menu" onClick={() => setOpen(true)}><Menu className="h-5 w-5" /></Button>
          <div className="hidden text-xs text-subtle lg:block"><HostLabel /> · DGX Spark</div>
          <Link href="/" className="text-sm font-semibold lg:hidden">Spark AI Hub</Link>
          <UserMenu />
        </header>
        <main id="main" className="mx-auto w-full max-w-[1500px] flex-1 px-3 pb-24 pt-4 sm:px-6 sm:py-6 lg:pb-6">{children}</main>
        <MobileTabs onMore={() => setOpen(true)} />
        <TermsGate />
      </div>
    </div>
  );
}

function HostLabel() {
  const [host, setHost] = useState("");
  useEffect(() => setHost(window.location.hostname.split(".")[0]), []);
  return <>{host || "Spark AI Hub"}</>;
}
