"use client";

import { useState, useRef, useEffect } from "react";
import { Info, X, ShieldCheck, Zap, TrendingUp, FlaskConical } from "lucide-react";
import type { ModelMeta } from "@/lib/modelMeta";
import { MODEL_SECTIONS } from "@/lib/modelMeta";

function SectionIcon({ section }: { section: string }) {
  if (section === "green_up") return <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />;
  if (section === "dynamics") return <Zap className="h-3.5 w-3.5 text-amber-400" />;
  if (section === "directional") return <TrendingUp className="h-3.5 w-3.5 text-sky-400" />;
  return <FlaskConical className="h-3.5 w-3.5 text-zinc-400" />;
}

export function ModelInfoPopover({ meta }: { meta: ModelMeta }) {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Close when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isOpen]);

  const section = MODEL_SECTIONS[meta.section];

  return (
    <div
      ref={containerRef}
      className="relative inline-flex items-center"
      onMouseEnter={() => setIsOpen(true)}
      onMouseLeave={() => setIsOpen(false)}
    >
      <button
        type="button"
        aria-label={`About ${meta.displayName}`}
        aria-expanded={isOpen}
        onClick={(e) => {
          e.stopPropagation();
          setIsOpen(!isOpen);
        }}
        className="rounded p-0.5 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/80 transition-colors focus:outline-none focus:ring-1 focus:ring-phoenix-500"
      >
        <Info className="h-3.5 w-3.5" />
      </button>

      {isOpen && (
        <div
          role="tooltip"
          className="absolute left-6 top-0 z-50 w-80 sm:w-96 rounded-xl border border-zinc-700/90 bg-zinc-900/98 p-4 text-left shadow-2xl backdrop-blur-md transition-all duration-150 animate-in fade-in zoom-in-95 pointer-events-auto"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-start justify-between gap-2 border-b border-zinc-800 pb-3">
            <div className="space-y-1">
              <div className="flex items-center gap-1.5 flex-wrap">
                <SectionIcon section={meta.section} />
                <h3 className="font-semibold text-zinc-100 text-sm">{meta.displayName}</h3>
                {meta.badge && (
                  <span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium tracking-wide uppercase ${meta.badgeClass ?? "bg-zinc-800 text-zinc-300 border-zinc-700"}`}>
                    {meta.badge}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2 text-[11px] text-zinc-400">
                <span className="font-mono text-zinc-300 bg-zinc-950 px-1.5 py-0.5 rounded border border-zinc-800">
                  {meta.code}
                </span>
                <span>•</span>
                <span className="text-zinc-400">{section.title}</span>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setIsOpen(false)}
              className="rounded p-1 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>

          {/* Body */}
          <div className="mt-3 space-y-3 text-xs leading-relaxed">
            <div>
              <span className="font-semibold text-zinc-300 uppercase tracking-wider text-[10px] block mb-0.5">
                Strategy Mandate
              </span>
              <p className="text-zinc-300">{meta.description}</p>
            </div>

            <div className="rounded-lg bg-zinc-950/70 p-2.5 border border-zinc-800/80">
              <span className="font-semibold text-emerald-400 uppercase tracking-wider text-[10px] block mb-1">
                How Trading Is Done
              </span>
              <p className="text-zinc-300 text-[11.5px] leading-relaxed">{meta.mechanics}</p>
            </div>

            {/* Key Specs Grid */}
            <div className="grid grid-cols-2 gap-2 pt-1 border-t border-zinc-800/60 text-[11px]">
              <div>
                <span className="text-zinc-500 block">Target Odds</span>
                <span className="font-medium text-zinc-200">{meta.keyStats.targetOdds}</span>
              </div>
              <div>
                <span className="text-zinc-500 block">Required Edge</span>
                <span className="font-medium text-zinc-200">{meta.keyStats.minEdge}</span>
              </div>
              <div>
                <span className="text-zinc-500 block">Exit Strategy</span>
                <span className="font-medium text-emerald-300">{meta.keyStats.exitRule}</span>
              </div>
              <div>
                <span className="text-zinc-500 block">Risk Safeguard</span>
                <span className="font-medium text-zinc-300">{meta.keyStats.lossProtection}</span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
