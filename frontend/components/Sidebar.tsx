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
  const statusColor = isHealthy ? "bg-green-500" : isDegraded ? "bg-amber-500" : "bg-red-500";
  const statusLabel = isHealthy ? "System Healthy" : isDegraded ? "System Degraded" : health ? "System Unhealthy" : "Connecting...";

  return (
    <aside className="fixed left-0 top-0 h-screen w-64 bg-zinc-900 border-r border-zinc-800 flex flex-col">
      {/* Logo */}
      <div className="p-6 border-b border-zinc-800">
        <h1 className="text-xl font-bold text-phoenix-500">PHOENIX</h1>
        <p className="text-xs text-zinc-500 mt-1">Cricket Betting RL</p>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-4 space-y-1">
        {navItems.map((item) => {
          const isActive = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 px-4 py-3 rounded-lg text-sm transition-colors ${
                isActive
                  ? "bg-phoenix-600/20 text-phoenix-400 font-medium"
                  : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800"
              }`}
            >
              <span className="text-lg">{item.icon}</span>
              {item.label}
            </Link>
          );
        })}
      </nav>

      {/* Status footer */}
      <div className="p-4 border-t border-zinc-800">
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${statusColor} ${isHealthy ? "animate-pulse" : ""}`} />
          <span className="text-xs text-zinc-500">{statusLabel}</span>
        </div>
      </div>
    </aside>
  );
}
