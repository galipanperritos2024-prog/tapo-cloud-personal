"use client";

import { Activity, Bell, Camera, Cloud, Film, LayoutDashboard, LogOut, Settings } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect } from "react";
import useSWR from "swr";
import { api, fetcher } from "@/lib/api";
import { Spinner } from "./ui";

const NAV = [
  { href: "/", label: "Inicio", icon: LayoutDashboard },
  { href: "/cameras/", label: "Cámaras", icon: Camera },
  { href: "/events/", label: "Eventos", icon: Bell },
  { href: "/recordings/", label: "Grabaciones", icon: Film },
  { href: "/drive/", label: "Google Drive", icon: Cloud },
  { href: "/system/", label: "Sistema", icon: Activity },
  { href: "/settings/", label: "Configuración", icon: Settings },
];

interface AuthStatus {
  setup_required: boolean;
  authenticated: boolean;
  username: string | null;
}

function isActive(pathname: string, href: string) {
  if (href === "/") return pathname === "/";
  return pathname.startsWith(href) || (href === "/cameras/" && pathname.startsWith("/camera/"));
}

export default function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const isLogin = pathname.startsWith("/login");
  const { data: auth } = useSWR<AuthStatus>(isLogin ? null : "/api/auth/status", fetcher);
  const { data: unread } = useSWR<{ unacknowledged: number }>(auth?.authenticated ? "/api/events?limit=1" : null, fetcher, {
    refreshInterval: 15000,
  });

  useEffect(() => {
    if (auth && !auth.authenticated) router.replace("/login/");
  }, [auth, router]);

  if (isLogin) return <>{children}</>;
  if (!auth?.authenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center text-muted">
        <Spinner size={24} />
      </div>
    );
  }

  const logout = async () => {
    await api("/api/auth/logout", { method: "POST" }).catch(() => undefined);
    window.location.href = "/login/";
  };

  return (
    <div className="flex min-h-screen">
      {/* Sidebar (desktop) */}
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col border-r border-line bg-surface px-4 py-6 lg:flex">
        <Link href="/" className="mb-8 flex items-center gap-3 px-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent">
            <Camera size={18} className="text-white" />
          </div>
          <div>
            <div className="text-sm font-semibold leading-tight">Tapo Cloud</div>
            <div className="text-xs text-muted">Personal</div>
          </div>
        </Link>
        <nav className="flex flex-1 flex-col gap-1">
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors ${
                isActive(pathname, href) ? "bg-accent/15 text-accent" : "text-muted hover:bg-surface-2 hover:text-fg"
              }`}
            >
              <Icon size={18} />
              <span className="flex-1">{label}</span>
              {href === "/events/" && !!unread?.unacknowledged && (
                <span className="rounded-full bg-err px-2 py-0.5 text-[10px] font-semibold text-white">{unread.unacknowledged > 99 ? "99+" : unread.unacknowledged}</span>
              )}
            </Link>
          ))}
        </nav>
        <div className="mt-4 flex items-center justify-between border-t border-line px-2 pt-4 text-sm">
          <span className="truncate text-muted">{auth.username}</span>
          <button onClick={logout} className="btn-ghost p-2" title="Cerrar sesión" aria-label="Cerrar sesión">
            <LogOut size={16} />
          </button>
        </div>
      </aside>

      {/* Main */}
      <main className="min-w-0 flex-1 px-4 pt-6 pb-24 sm:px-6 lg:px-10 lg:pb-10">{children}</main>

      {/* Bottom nav (mobile) */}
      <nav className="fixed inset-x-0 bottom-0 z-40 flex justify-around border-t border-line bg-surface/95 px-1 py-1.5 backdrop-blur lg:hidden">
        {NAV.slice(0, 5).map(({ href, label, icon: Icon }) => (
          <Link key={href} href={href} className={`flex flex-col items-center gap-0.5 rounded-lg px-2 py-1 text-[10px] ${isActive(pathname, href) ? "text-accent" : "text-muted"}`}>
            <Icon size={20} />
            {label.split(" ")[0]}
          </Link>
        ))}
        <Link href="/settings/" className={`flex flex-col items-center gap-0.5 rounded-lg px-2 py-1 text-[10px] ${isActive(pathname, "/settings/") || isActive(pathname, "/system/") ? "text-accent" : "text-muted"}`}>
          <Settings size={20} />
          Más
        </Link>
      </nav>
    </div>
  );
}
