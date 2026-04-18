import type { Config } from "tailwindcss";

// Brand tokens mirror karna/tui/design_tokens.py.
// Keep these in sync with the TUI palette — they are the source of truth.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        brand: { DEFAULT: "#3C73BD", hover: "#5A8FCC" },
        surface: { DEFAULT: "#0E0F12", raised: "#1A1D23" },
        ink: {
          DEFAULT: "#E6E8EC",
          secondary: "#A0A4AD",
          tertiary: "#5F6472",
        },
        accent: {
          cyan: "#87CEEB",
          success: "#7DCFA1",
          danger: "#E87C7C",
          thinking: "#9F7AEA",
        },
        border: {
          subtle: "#2A2F38",
          accent: "#3C73BD",
        },
      },
      fontFamily: {
        sans: [
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "Liberation Mono",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};

export default config;
