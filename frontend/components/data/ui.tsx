import type { ReactNode } from "react";

export function Section({
  title,
  subtitle,
  right,
  children,
}: {
  title: string;
  subtitle?: string;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="min-w-0 rounded-xl border border-zinc-800/60 bg-zinc-900/60 backdrop-blur-md p-4 sm:p-5 space-y-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-zinc-100">{title}</h2>
          {subtitle && <p className="text-sm text-zinc-400 mt-1">{subtitle}</p>}
        </div>
        {right && <div className="shrink-0">{right}</div>}
      </div>
      {children}
    </section>
  );
}

export function ErrorBanner({ message, stale }: { message: string; stale: boolean }) {
  return (
    <div className="rounded-lg border border-red-800 bg-red-900/30 p-3 text-sm text-red-300">
      Could not load ({message}).{" "}
      {stale ? "The figures below are from the last successful load." : "Nothing has loaded yet."}
    </div>
  );
}

export function Muted({ children }: { children: ReactNode }) {
  return <p className="text-sm text-zinc-500">{children}</p>;
}

export function Badge({ className, children }: { className: string; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded-md border px-2 py-0.5 text-xs font-semibold tracking-wide ${className}`}
    >
      {children}
    </span>
  );
}
