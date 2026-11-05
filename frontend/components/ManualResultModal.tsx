"use client";

import { useState, type FormEvent } from "react";
import { API_BASE } from "@/lib/api";
import type { ResultOutcome, ResultRequest, ResultWritten } from "@/app/data/types";
import { readErrorDetail } from "@/components/data/format";

// Enter a result for a real match from a public scorecard. POST /data/results (S91 rules): the API validates the
// winner against the registry, refuses to overwrite a trusted result (409) and settles the match's paper bets.

export interface ManualResultTarget {
  match_id: string;
  team_home: string;
  team_away: string;
  competition: string;
}

interface ManualResultModalProps {
  target: ManualResultTarget;
  onClose: () => void;
  onWritten: (result: ResultWritten) => void;
}

const OUTCOMES: { value: ResultOutcome; label: string }[] = [
  { value: "winner", label: "Winner" },
  { value: "tie", label: "Tie" },
  { value: "no_result", label: "No result" },
];

function resultLabel(written: ResultWritten["result"]): string {
  if (written.result_type === "win") {
    return `Winner: ${written.winner}${written.margin ? ` by ${written.margin}` : ""}`;
  }
  return written.result_type.replace(/_/g, " ");
}

export function ManualResultModal({ target, onClose, onWritten }: ManualResultModalProps) {
  const [outcome, setOutcome] = useState<ResultOutcome>("winner");
  const [winner, setWinner] = useState("");
  const [margin, setMargin] = useState("");
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<{ status: string; message: string } | null>(null);
  const [written, setWritten] = useState<ResultWritten | null>(null);

  const chooseOutcome = (next: ResultOutcome) => {
    setOutcome(next);
    if (next !== "winner") {
      setWinner("");
      setMargin("");
    }
  };

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const trimmedUrl = url.trim();
    if (!/^https?:\/\/\S{3,}$/i.test(trimmedUrl)) {
      setError({ status: "Check the form", message: "The scorecard URL is required and must start with http." });
      return;
    }
    if (outcome === "winner" && winner !== target.team_home && winner !== target.team_away) {
      setError({ status: "Check the form", message: "Choose the winner: one of the two teams." });
      return;
    }

    const body: ResultRequest = { match_id: target.match_id, outcome, url: trimmedUrl };
    if (outcome === "winner") {
      body.winner = winner;
      if (margin.trim()) body.margin = margin.trim();
    }

    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/data/results`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const status = res.status === 409 ? "Refused: a trusted result exists" : res.status === 422 ? "Refused: invalid" : "Failed";
        setError({ status, message: await readErrorDetail(res) });
        return;
      }
      const data = (await res.json()) as ResultWritten;
      setWritten(data);
      onWritten(data);
    } catch {
      setError({ status: "Failed", message: "The API could not be reached. Nothing was written." });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 p-0 sm:items-center sm:p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="manual-result-title"
        className="max-h-[92vh] w-full overflow-y-auto rounded-t-xl border border-zinc-700 bg-zinc-900 p-5 shadow-2xl sm:max-w-lg sm:rounded-xl"
      >
        <h2 id="manual-result-title" className="text-lg font-bold text-zinc-100">
          Enter result
        </h2>
        <p className="mt-1 text-sm text-zinc-400">
          From a public scorecard. Written to the match and its paper bets settled at once.
        </p>

        <div className="mt-4 rounded-lg bg-zinc-800/70 p-3 text-center text-sm">
          <p className="font-medium text-zinc-100">{target.team_home}</p>
          <p className="my-0.5 text-xs text-zinc-500">v</p>
          <p className="font-medium text-zinc-100">{target.team_away}</p>
          <p className="mt-2 text-xs text-zinc-500">
            {target.competition || "competition not recorded"} · {target.match_id}
          </p>
        </div>

        {written ? (
          <div className="mt-5 space-y-4">
            <div className="rounded-lg border border-emerald-700 bg-emerald-900/20 p-4 text-sm">
              <p className="font-semibold text-emerald-300">Result written</p>
              <dl className="mt-3 space-y-1.5 text-zinc-300">
                <div className="flex justify-between gap-3">
                  <dt className="text-zinc-500">Result</dt>
                  <dd className="text-right">{resultLabel(written.result)}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-zinc-500">Paper bets settled</dt>
                  <dd className="text-right font-mono">{written.paper_bets_settled}</dd>
                </div>
                {Object.keys(written.bets_settled).length > 0 && (
                  <div className="flex justify-between gap-3">
                    <dt className="text-zinc-500">By status</dt>
                    <dd className="text-right">
                      {Object.entries(written.bets_settled)
                        .map(([status, count]) => `${status} ${count}`)
                        .join(", ")}
                    </dd>
                  </div>
                )}
                <div className="flex justify-between gap-3">
                  <dt className="text-zinc-500">Replaced</dt>
                  <dd className="text-right">{written.replaced ?? "no earlier result"}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-zinc-500">Source</dt>
                  <dd className="min-w-0 break-all text-right">{written.result.source}</dd>
                </div>
              </dl>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="w-full rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500"
            >
              Done
            </button>
          </div>
        ) : (
          <form onSubmit={submit} className="mt-5 space-y-5">
            <fieldset>
              <legend className="mb-2 text-sm font-medium text-zinc-300">Outcome</legend>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                {OUTCOMES.map((o) => (
                  <label
                    key={o.value}
                    className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors ${
                      outcome === o.value
                        ? "border-emerald-500 bg-emerald-600/20 text-zinc-100"
                        : "border-zinc-700 bg-zinc-800 text-zinc-300 hover:border-zinc-500"
                    }`}
                  >
                    <input
                      type="radio"
                      name="outcome"
                      value={o.value}
                      checked={outcome === o.value}
                      onChange={() => chooseOutcome(o.value)}
                      className="accent-emerald-500"
                    />
                    {o.label}
                  </label>
                ))}
              </div>
            </fieldset>

            {outcome === "winner" && (
              <>
                <div>
                  <label htmlFor="result-winner" className="mb-2 block text-sm font-medium text-zinc-300">
                    Winner
                  </label>
                  <select
                    id="result-winner"
                    value={winner}
                    onChange={(e) => setWinner(e.target.value)}
                    className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100"
                  >
                    <option value="">Choose a team</option>
                    <option value={target.team_home}>{target.team_home}</option>
                    <option value={target.team_away}>{target.team_away}</option>
                  </select>
                </div>
                <div>
                  <label htmlFor="result-margin" className="mb-2 block text-sm font-medium text-zinc-300">
                    Margin <span className="text-zinc-500">(optional)</span>
                  </label>
                  <input
                    id="result-margin"
                    type="text"
                    value={margin}
                    onChange={(e) => setMargin(e.target.value)}
                    placeholder="e.g. 5 wickets, 23 runs"
                    maxLength={60}
                    className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600"
                  />
                </div>
              </>
            )}

            <div>
              <label htmlFor="result-url" className="mb-2 block text-sm font-medium text-zinc-300">
                Scorecard URL
              </label>
              <input
                id="result-url"
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://"
                required
                className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600"
              />
            </div>

            {error && (
              <div role="alert" className="rounded-lg border border-red-800 bg-red-900/30 p-3 text-sm text-red-300">
                <p className="font-semibold">{error.status}</p>
                <p className="mt-1 break-words">{error.message}</p>
              </div>
            )}

            <div className="flex flex-col-reverse gap-3 pt-1 sm:flex-row">
              <button
                type="button"
                onClick={onClose}
                disabled={submitting}
                className="flex-1 rounded-lg bg-zinc-700 px-4 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-zinc-600 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting}
                className="flex-1 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-50"
              >
                {submitting ? "Writing..." : "Write result"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
