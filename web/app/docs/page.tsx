import Link from "next/link";

export const metadata = {
  title: "Docs — Nellie",
};

// Docs are authored in the repo's `docs/` tree. Until we wire up a MDX
// pipeline (follow-up PR), we link out to GitHub so the content stays in
// one place.
export default function DocsPage() {
  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <h1 className="text-3xl font-semibold tracking-tight">Docs</h1>
        <p className="text-ink-secondary">
          Full documentation currently lives in the repository. A first-class
          docs site is on the roadmap.
        </p>
      </header>

      <ul className="space-y-3 text-sm">
        <DocLink
          href="https://github.com/Viraj0518/Karna-GenAI-CLI/blob/main/GETTING_STARTED.md"
          label="Getting started"
        />
        <DocLink
          href="https://github.com/Viraj0518/Karna-GenAI-CLI/tree/main/docs"
          label="docs/ — reference, design notes, SOPs"
        />
        <DocLink
          href="https://github.com/Viraj0518/Karna-GenAI-CLI/blob/main/README.md"
          label="README"
        />
        <DocLink
          href="https://github.com/Viraj0518/Karna-GenAI-CLI/blob/main/CHANGELOG.md"
          label="Changelog"
        />
      </ul>

      <p className="text-sm text-ink-tertiary">
        Back to{" "}
        <Link
          href="/"
          className="text-brand hover:text-brand-hover underline underline-offset-4"
        >
          home
        </Link>
        .
      </p>
    </div>
  );
}

function DocLink({ href, label }: { href: string; label: string }) {
  return (
    <li>
      <a
        className="group flex items-center justify-between rounded-md border border-border-subtle/60 bg-surface-raised/30 px-4 py-3 transition hover:border-border-accent/70 hover:bg-surface-raised/60"
        href={href}
        target="_blank"
        rel="noreferrer"
      >
        <span className="text-ink">{label}</span>
        <span className="font-mono text-xs text-ink-tertiary group-hover:text-brand">
          ↗
        </span>
      </a>
    </li>
  );
}
