interface StatCardProps {
  label: string;
  value: string;
  color?: "green" | "red" | "yellow" | "default";
}

const colorMap = {
  green: "text-emerald-400",
  red: "text-red-400",
  yellow: "text-amber-400",
  default: "text-zinc-100",
};

export function StatCard({ label, value, color = "default" }: StatCardProps) {
  return (
    <div className="stat-card">
      <p className="text-xs text-zinc-500 uppercase tracking-wider mb-2">
        {label}
      </p>
      <p className={`text-2xl font-bold ${colorMap[color]}`}>{value}</p>
    </div>
  );
}
