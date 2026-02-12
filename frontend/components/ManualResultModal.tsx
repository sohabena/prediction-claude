"use client";

import { useState } from "react";
import { API_BASE } from "@/lib/api";

interface ManualResultModalProps {
  matchId: string;
  teamHome: string;
  teamAway: string;
  onClose: () => void;
  onSuccess: () => void;
}

export function ManualResultModal({
  matchId,
  teamHome,
  teamAway,
  onClose,
  onSuccess,
}: ManualResultModalProps) {
  const [winner, setWinner] = useState<string>("");
  const [resultType, setResultType] = useState<string>("win");
  const [margin, setMargin] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    if (resultType === "win" && !winner) {
      setError("Please select a winner");
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      const loser = winner === teamHome ? teamAway : teamHome;
      const params = new URLSearchParams({
        winner: resultType === "win" ? winner : "",
        loser: resultType === "win" ? loser : "",
        result_type: resultType,
        margin: margin,
      });

      const res = await fetch(
        `${API_BASE}/matches/${matchId}/result?${params.toString()}`,
        { method: "POST" }
      );

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${res.status}`);
      }

      onSuccess();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit result");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
      <div className="bg-zinc-900 border border-zinc-700 rounded-xl p-6 w-full max-w-md mx-4">
        <h2 className="text-xl font-bold mb-4">Submit Manual Result</h2>
        <p className="text-sm text-zinc-400 mb-4">
          Use this when automatic result collection fails.
        </p>

        <div className="space-y-4">
          {/* Match info */}
          <div className="bg-zinc-800 rounded-lg p-3 text-center">
            <p className="font-medium">{teamHome}</p>
            <p className="text-xs text-zinc-500 my-1">vs</p>
            <p className="font-medium">{teamAway}</p>
          </div>

          {/* Result type */}
          <div>
            <label className="block text-sm font-medium text-zinc-300 mb-2">
              Result Type
            </label>
            <select
              value={resultType}
              onChange={(e) => {
                setResultType(e.target.value);
                if (e.target.value !== "win") setWinner("");
              }}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm"
            >
              <option value="win">Win</option>
              <option value="tie">Tie</option>
              <option value="draw">Draw</option>
              <option value="no_result">No Result</option>
              <option value="abandoned">Abandoned</option>
            </select>
          </div>

          {/* Winner selection (only for win) */}
          {resultType === "win" && (
            <div>
              <label className="block text-sm font-medium text-zinc-300 mb-2">
                Winner
              </label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => setWinner(teamHome)}
                  className={`py-2 px-3 rounded-lg text-sm font-medium border transition-colors ${
                    winner === teamHome
                      ? "bg-emerald-600 border-emerald-500 text-white"
                      : "bg-zinc-800 border-zinc-700 text-zinc-300 hover:border-zinc-500"
                  }`}
                >
                  {teamHome}
                </button>
                <button
                  type="button"
                  onClick={() => setWinner(teamAway)}
                  className={`py-2 px-3 rounded-lg text-sm font-medium border transition-colors ${
                    winner === teamAway
                      ? "bg-emerald-600 border-emerald-500 text-white"
                      : "bg-zinc-800 border-zinc-700 text-zinc-300 hover:border-zinc-500"
                  }`}
                >
                  {teamAway}
                </button>
              </div>
            </div>
          )}

          {/* Margin (optional) */}
          {resultType === "win" && (
            <div>
              <label className="block text-sm font-medium text-zinc-300 mb-2">
                Victory Margin (optional)
              </label>
              <input
                type="text"
                value={margin}
                onChange={(e) => setMargin(e.target.value)}
                placeholder="e.g., 5 wickets, 23 runs"
                className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm"
              />
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="bg-red-900/30 border border-red-800 rounded-lg p-3 text-sm text-red-400">
              {error}
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-3 mt-6">
            <button
              onClick={onClose}
              disabled={submitting}
              className="flex-1 py-2 px-4 rounded-lg bg-zinc-700 hover:bg-zinc-600 text-zinc-300 text-sm font-medium transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSubmit}
              disabled={submitting}
              className="flex-1 py-2 px-4 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium transition-colors disabled:opacity-50"
            >
              {submitting ? "Submitting..." : "Submit Result"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
