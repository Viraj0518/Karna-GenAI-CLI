import { Hero } from "./components/Hero";
import { InstallTabs } from "./components/InstallTabs";
import Link from "next/link";

export default function HomePage() {
  return (
    <div className="space-y-20">
      <Hero />

      <section className="space-y-6">
        <h2 className="text-sm font-mono uppercase tracking-wider text-ink-tertiary">
          Install
        </h2>
        <InstallTabs />
        <p className="text-sm text-ink-secondary">
          Full options on the{" "}
          <Link
            href="/download"
            className="text-brand hover:text-brand-hover underline underline-offset-4"
          >
            download page
          </Link>
          .
        </p>
      </section>

      <section className="grid gap-6 sm:grid-cols-3">
        <Feature
          title="Any model"
          body="OpenAI, Anthropic, Google, local Ollama — one harness, one UX."
        />
        <Feature
          title="Your terminal"
          body="Rich TUI built for keyboard flow. Vim mode, slash commands, plan mode."
        />
        <Feature
          title="Your keys"
          body="Local-first. Your API keys never leave your machine."
        />
      </section>

      <section className="rounded-lg border border-border-subtle/60 bg-surface-raised/40 p-6">
        <p className="text-sm text-ink-secondary">
          Docs and advanced usage →{" "}
          <Link
            href="/docs"
            className="text-brand hover:text-brand-hover underline underline-offset-4"
          >
            /docs
          </Link>
        </p>
      </section>
    </div>
  );
}

function Feature({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-lg border border-border-subtle/60 bg-surface-raised/30 p-5">
      <h3 className="mb-2 font-mono text-sm text-brand">{title}</h3>
      <p className="text-sm leading-relaxed text-ink-secondary">{body}</p>
    </div>
  );
}
