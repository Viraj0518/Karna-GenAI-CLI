import Link from "next/link";

const NAV = [
  { href: "/", label: "Home" },
  { href: "/download", label: "Download" },
  { href: "/docs", label: "Docs" },
  {
    href: "https://github.com/Viraj0518/Karna-GenAI-CLI",
    label: "GitHub",
    external: true,
  },
] as const;

export function Header() {
  return (
    <header className="border-b border-border-subtle/60 bg-surface/80 backdrop-blur">
      <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
        <Link
          href="/"
          className="font-mono text-lg tracking-tight text-ink hover:text-brand"
        >
          nellie
        </Link>
        <nav className="flex items-center gap-6 text-sm">
          {NAV.map((item) =>
            "external" in item && item.external ? (
              <a
                key={item.href}
                href={item.href}
                target="_blank"
                rel="noreferrer"
                className="text-ink-secondary hover:text-brand"
              >
                {item.label}
              </a>
            ) : (
              <Link
                key={item.href}
                href={item.href}
                className="text-ink-secondary hover:text-brand"
              >
                {item.label}
              </Link>
            ),
          )}
        </nav>
      </div>
    </header>
  );
}
