"use client";

import { useState } from "react";

type TabKey = "pip" | "curl" | "docker" | "brew";

const TABS: { key: TabKey; label: string; command: string; note?: string }[] = [
  {
    key: "pip",
    label: "pip",
    command: "pip install nellie",
    note: "Requires Python 3.11+.",
  },
  {
    key: "curl",
    label: "curl",
    command:
      "curl -fsSL https://nellie.kaeva.app/install.sh | sh",
    note: "One-liner bootstrap — inspects and installs.",
  },
  {
    key: "docker",
    label: "docker",
    command: "docker run --rm -it ghcr.io/viraj0518/nellie:latest",
    note: "Pinned image, no local Python needed.",
  },
  {
    key: "brew",
    label: "brew",
    command: "brew install viraj0518/tap/nellie",
    note: "macOS / Linuxbrew. Tap lands with v0.2.",
  },
];

export function InstallTabs() {
  const [active, setActive] = useState<TabKey>("pip");
  const current = TABS.find((t) => t.key === active)!;

  return (
    <div className="overflow-hidden rounded-lg border border-border-subtle/60 bg-surface-raised/40">
      <div
        className="flex border-b border-border-subtle/60 font-mono text-xs"
        role="tablist"
        aria-label="Install methods"
      >
        {TABS.map((tab) => {
          const selected = tab.key === active;
          return (
            <button
              key={tab.key}
              role="tab"
              aria-selected={selected}
              onClick={() => setActive(tab.key)}
              className={
                "px-4 py-3 transition " +
                (selected
                  ? "bg-surface-raised text-brand"
                  : "text-ink-tertiary hover:text-ink-secondary")
              }
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      <div className="p-4">
        <pre className="overflow-x-auto font-mono text-sm text-ink">
          <code>
            <span className="text-ink-tertiary">$ </span>
            {current.command}
          </code>
        </pre>
        {current.note && (
          <p className="mt-3 text-xs text-ink-tertiary">{current.note}</p>
        )}
      </div>
    </div>
  );
}
