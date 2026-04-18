export const metadata = {
  title: "Console — Nellie",
};

// Placeholder for the future conversation browser. The real UI will
// connect to a Nellie instance running locally on port 8765 and render
// session history, tool calls, and live streams.
export default function ConsolePage() {
  return (
    <div className="space-y-10">
      <header className="space-y-3">
        <h1 className="text-3xl font-semibold tracking-tight">Console</h1>
        <p className="text-ink-secondary">
          The conversation browser will live here.
        </p>
      </header>

      <div className="rounded-lg border border-dashed border-border-subtle bg-surface-raised/30 p-10 text-center">
        <p className="font-mono text-sm text-brand">Coming soon</p>
        <p className="mt-3 text-sm text-ink-secondary">
          Connect to a local Nellie instance on{" "}
          <code className="rounded bg-surface-raised px-1.5 py-0.5 font-mono text-xs text-ink">
            localhost:8765
          </code>
          .
        </p>
        <p className="mt-2 text-xs text-ink-tertiary">
          Start your agent with <code className="font-mono">nellie serve</code>{" "}
          and return here once the HTTP bridge lands.
        </p>
      </div>
    </div>
  );
}
