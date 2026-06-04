import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";

import { AuthNav } from "@/components/auth/AuthNav";
import { ClerkOptionalProvider } from "@/components/auth/ClerkOptionalProvider";
import { apiBaseUrl } from "@/lib/api";

export const metadata: Metadata = {
  title: "PlanMyAgents",
  description:
    "Discovery, verification, and benchmark-driven routing across MCP servers, A2A agents, AI agents, and APIs.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <ClerkOptionalProvider>
      <html lang="en" className="bg-ink-50">
        <body className="min-h-screen bg-ink-50 text-ink-800 antialiased">
          <header className="border-b border-ink-200 bg-white">
            <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
              <Link href="/" className="flex items-center gap-3">
                <div className="h-8 w-8 rounded-lg bg-ink-900 text-center text-sm font-semibold leading-8 text-white">
                  AM
                </div>
                <div>
                  <div className="text-base font-semibold text-ink-900">
                    PlanMyAgents
                  </div>
                  <div className="text-xs text-ink-400">
                    Discovery &middot; Verification &middot; Benchmarks
                  </div>
                </div>
              </Link>
              <nav className="flex items-center gap-5 text-sm">
                <Link
                  href="/"
                  className="rounded-md bg-ink-900 px-3 py-1.5 font-medium text-white hover:bg-ink-800"
                >
                  Goal
                </Link>
                <div className="flex items-center gap-3 border-l border-ink-200 pl-5 text-xs uppercase tracking-wide text-ink-400">
                  <span>Browse</span>
                  <Link
                    href="/categories"
                    className="text-ink-600 normal-case tracking-normal hover:text-ink-900"
                  >
                    Categories
                  </Link>
                  <Link
                    href="/leaderboards"
                    className="text-ink-600 normal-case tracking-normal hover:text-ink-900"
                  >
                    Leaderboards
                  </Link>
                  <Link
                    href="/search"
                    className="text-ink-600 normal-case tracking-normal hover:text-ink-900"
                  >
                    Search
                  </Link>
                  <Link
                    href="/open-mcp-opportunities"
                    className="text-ink-600 normal-case tracking-normal hover:text-ink-900"
                  >
                    Open MCP opportunities
                  </Link>
                  <Link
                    href="/discovery-gaps"
                    className="text-ink-600 normal-case tracking-normal hover:text-ink-900"
                  >
                    Discovery gaps
                  </Link>
                </div>
                {/* Resolves at request time via apiBaseUrl(); previously
                    hard-coded `http://localhost:8000/docs` which broke the
                    API-docs link on any non-localhost deploy. */}
                <a
                  href={`${apiBaseUrl()}/docs`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-ink-400 hover:text-ink-900"
                >
                  API
                </a>
                <AuthNav />
              </nav>
            </div>
          </header>
          <main className="mx-auto max-w-6xl px-6 py-10">{children}</main>
          <footer className="border-t border-ink-200 bg-white">
            <div className="mx-auto max-w-6xl px-6 py-6 text-xs text-ink-400">
              All metrics are computed from the local discovery + benchmark stores.
              Numbers labelled <span className="tag-neutral">synthetic</span> come
              from mock adapters and are not real-task performance.
            </div>
          </footer>
        </body>
      </html>
    </ClerkOptionalProvider>
  );
}
