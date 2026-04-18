import { InstallTabs } from "../components/InstallTabs";

export const metadata = {
  title: "Download — Nellie",
};

export default function DownloadPage() {
  return (
    <div className="space-y-10">
      <header className="space-y-3">
        <h1 className="text-3xl font-semibold tracking-tight">Download</h1>
        <p className="text-ink-secondary">
          Pick the install path that matches your setup. All options ship the
          same binary — differences are packaging only.
        </p>
      </header>

      <InstallTabs />

      <section className="space-y-3 text-sm text-ink-secondary">
        <h2 className="font-mono text-xs uppercase tracking-wider text-ink-tertiary">
          Verify
        </h2>
        <pre className="rounded-md border border-border-subtle/60 bg-surface-raised/50 p-4 font-mono text-xs text-ink">
          {"nellie --version\nnellie doctor"}
        </pre>
      </section>
    </div>
  );
}
