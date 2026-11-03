import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";

export const metadata: Metadata = {
  title: "PHOENIX",
  description: "PHOENIX paper trading and data",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="bg-zinc-950 text-zinc-100 min-h-screen selection:bg-phoenix-500/30">
        <div className="flex min-h-screen flex-col md:flex-row">
          <Sidebar />
          <main className="flex-1 min-w-0 md:ml-64 p-4 md:p-8 fade-in">{children}</main>
        </div>
      </body>
    </html>
  );
}
