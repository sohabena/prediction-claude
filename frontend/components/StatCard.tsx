interface StatCardProps {
  label: string;
  value: string;
  color?: "green" | "red" | "yellow" | "default";
}

const colorMap = {
  green: "text-emerald-400 drop-shadow-[0_0_8px_rgba(52,211,153,0.5)]",
  red: "text-red-400 drop-shadow-[0_0_8px_rgba(248,113,113,0.5)]",
  yellow: "text-amber-400 drop-shadow-[0_0_8px_rgba(251,191,36,0.5)]",
  default: "text-zinc-100",
};

export function StatCard({ label, value, color = "default" }: StatCardProps) {
  return (
    <div className="stat-card relative overflow-hidden group">
      <div className="absolute inset-0 bg-gradient-to-br from-white/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none" />
      <p className="text-xs text-zinc-400 uppercase tracking-widest font-semibold mb-2 drop-shadow-sm">
        {label}
      </p>
      <p className={`text-3xl font-black tracking-tight ${colorMap[color]}`}>
        {value}
      </p>
    </div>
  );
}
