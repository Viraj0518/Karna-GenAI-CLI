import type { Metadata } from "next";
import "./globals.css";
import { Header } from "./components/Header";

export const metadata: Metadata = {
  title: "Nellie — Karna's AI agent harness",
  description:
    "Any model. Your terminal. Your keys. Nellie is the open CLI harness for the Karna agent stack.",
  metadataBase: new URL("https://nellie.kaeva.app"),
  openGraph: {
    title: "Nellie — Karna's AI agent harness",
    description: "Any model. Your terminal. Your keys.",
    url: "https://nellie.kaeva.app",
    siteName: "Nellie",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-surface text-ink antialiased">
        <Header />
        <main className="mx-auto max-w-5xl px-6 py-16">{children}</main>
        <footer className="border-t border-border-subtle/60 py-8 text-center text-xs text-ink-tertiary">
          <span className="font-mono">nellie</span> · part of the Karna stack ·{" "}
          <a
            className="hover:text-ink-secondary"
            href="https://github.com/Viraj0518/Karna-GenAI-CLI"
          >
            github
          </a>
        </footer>
      </body>
    </html>
  );
}
