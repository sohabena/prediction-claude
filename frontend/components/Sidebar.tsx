"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useApi } from "@/hooks/useApi";

const navItems = [
  { href: "/", label: "Dashboard", icon: "📊" },
  { href: "/training", label: "Training", icon: "🧠" },
  { href: "/trading", label: "Virtual Trading", icon: "💰" },
  { href: "/graduation", label: "Graduation", icon: "🎓" },
  { href: "/matches", label: "Live Matches", icon: "🏏" },
  { href: "/advisor", label: "Advisor", icon: "🎯" },
];

interface HealthResponse {
  status: string;
}

export function Sidebar() {
  const pathname = usePathname();
  const { data: health } = useApi<HealthResponse>("/health", 15000);

  const isHealthy = health?.status === "healthy";
  const isDegraded = health?.status === "degraded";
  const statusColor = isHealthy ? "bg-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.8)]" : isDegraded ? "bg-amber-500 shadow-[0_0_10px_rgba(245,158,11,0.8)]" : "bg-red-500 shadow-[0_0_10px_rgba(239,68,68,0.8)]";
  const statusLabel = isHealthy ? "System Healthy" : isDegraded ? "System Degraded" : health ? "System Unhealthy" : "Connecting...";

  return (
    <aside className="fixed left-0 top-0 h-screen w-64 bg-zinc-900/60 backdrop-blur-xl border-r border-zinc-800/50 flex flex-col shadow-2xl z-50">
      {/* Logo */}
      <div className="p-6 border-b border-zinc-800/50 relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-br from-phoenix-500/10 to-transparent pointer-events-none" />
        <h1 className="text-2xl font-black bg-clip-text text-transparent bg-gradient-to-r from-phoenix-400 to-phoenix-600 tracking-tight drop-shadow-sm">PHOENIX</h1>
        <p className="text-xs text-zinc-400 mt-1 font-medium tracking-wide">Cricket Betting RL</p>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-4 space-y-2">
        {navItems.map((item) => {
          const isActive = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 px-4 py-3 rounded-xl text-sm transition-all duration-300 ${
                isActive
                  ? "bg-gradient-to-r from-phoenix-500/20 to-phoenix-500/5 text-phoenix-400 font-semibold shadow-inner border border-phoenix-500/20"
                  : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50 hover:translate-x-1 border border-transparent"
              }`}
            >
              <span className="text-xl opacity-90 drop-shadow-sm">{item.icon}</span>
              {item.label}
            </Link>
          );
        })}
      </nav>

      {/* Status footer */}
      <div className="p-4 border-t border-zinc-800/50 bg-zinc-900/40 backdrop-blur-sm">
        <div className="flex items-center gap-3 px-2">
          <div className={`w-2.5 h-2.5 rounded-full ${statusColor} ${isHealthy ? "animate-pulse" : ""}`} />
          <span className="text-xs font-medium text-zinc-400 tracking-wide">{statusLabel}</span>
        </div>
      </div>
    </aside>
  );
}
