"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";
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
  const [mounted, setMounted] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const closeTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    setMounted(true);
  }, []);

  const updatePosition = useCallback(() => {
    if (!buttonRef.current) return;
    const rect = buttonRef.current.getBoundingClientRect();
    const popoverWidth = Math.min(384, window.innerWidth - 32);
    // Use actual rendered height if available, or a safe default of 440px
    const popoverHeight = popoverRef.current ? popoverRef.current.offsetHeight : 440;

    // Horizontal: default to right of button, adjust if off-screen
    let left = rect.right + 12;
    if (left + popoverWidth > window.innerWidth - 16) {
      left = Math.max(16, window.innerWidth - popoverWidth - 16);
    }

    // Vertical: align with button, pull upwards if near bottom of screen
    let top = rect.top - 8;
    if (top + popoverHeight > window.innerHeight - 16) {
      top = Math.max(16, window.innerHeight - popoverHeight - 16);
    }
    if (top < 16) {
      top = 16;
    }

    setCoords({ top, left });
  }, []);

  useEffect(() => {
    if (isOpen) {
      updatePosition();
      // Next frame to measure true offsetHeight after portal renders
      const frameId = requestAnimationFrame(updatePosition);

      const handleScroll = () => updatePosition();
      const handleResize = () => updatePosition();
      window.addEventListener("scroll", handleScroll, true);
      window.addEventListener("resize", handleResize);
      return () => {
        cancelAnimationFrame(frameId);
        window.removeEventListener("scroll", handleScroll, true);
        window.removeEventListener("resize", handleResize);
      };
    }
  }, [isOpen, updatePosition]);

  const handleMouseEnter = () => {
    if (closeTimeoutRef.current) {
      clearTimeout(closeTimeoutRef.current);
      closeTimeoutRef.current = null;
    }
    setIsOpen(true);
  };

  const handleMouseLeave = () => {
    closeTimeoutRef.current = setTimeout(() => {
      setIsOpen(false);
    }, 220);
  };

  // Click outside to close
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        popoverRef.current &&
        !popoverRef.current.contains(event.target as Node) &&
        buttonRef.current &&
        !buttonRef.current.contains(event.target as Node)
      ) {
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
    <>
      <button
        ref={buttonRef}
        type="button"
        aria-label={`About ${meta.displayName}`}
        aria-expanded={isOpen}
        onClick={(e) => {
          e.stopPropagation();
          setIsOpen((prev) => !prev);
        }}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        className="rounded p-0.5 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/80 transition-colors focus:outline-none focus:ring-1 focus:ring-phoenix-500 inline-flex items-center"
      >
        <Info className="h-3.5 w-3.5" />
      </button>

      {mounted && isOpen && coords && createPortal(
        <div
          ref={popoverRef}
          role="tooltip"
          style={{
            position: "fixed",
            top: `${coords.top}px`,
            left: `${coords.left}px`,
            zIndex: 99999,
            maxWidth: "calc(100vw - 32px)",
          }}
          className="w-80 sm:w-96 rounded-xl border border-zinc-700/90 bg-zinc-900/98 p-4 text-left shadow-2xl backdrop-blur-md animate-in fade-in zoom-in-95 pointer-events-auto"
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
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

          {/* Body with vertical scrolling if screen height is constrained */}
          <div className="mt-3 space-y-3 text-xs leading-relaxed max-h-[70vh] overflow-y-auto pr-1">
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
        </div>,
        document.body
      )}
    </>
  );
}
