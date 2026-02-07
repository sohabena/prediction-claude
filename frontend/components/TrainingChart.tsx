"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";

interface DataPoint {
  episode: number;
  episode_reward: number;
  win_rate: number;
  roi: number;
}

interface TrainingChartProps {
  data: DataPoint[];
  metric: "episode_reward" | "win_rate" | "roi";
  title: string;
}

const metricConfig = {
  episode_reward: { color: "#f59e0b", label: "Episode Reward" },
  win_rate: { color: "#10b981", label: "Win Rate" },
  roi: { color: "#6366f1", label: "ROI" },
};

export function TrainingChart({ data, metric, title }: TrainingChartProps) {
  const config = metricConfig[metric];

  return (
    <div className="chart-container">
      <h3 className="text-sm font-medium text-zinc-400 mb-4">{title}</h3>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#333" />
          <XAxis
            dataKey="episode"
            stroke="#666"
            tick={{ fill: "#888", fontSize: 12 }}
          />
          <YAxis stroke="#666" tick={{ fill: "#888", fontSize: 12 }} />
          <Tooltip
            contentStyle={{
              backgroundColor: "#1a1a1a",
              border: "1px solid #333",
              borderRadius: "8px",
            }}
            labelStyle={{ color: "#999" }}
          />
          <Legend />
          <Line
            type="monotone"
            dataKey={metric}
            stroke={config.color}
            name={config.label}
            dot={false}
            strokeWidth={2}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
