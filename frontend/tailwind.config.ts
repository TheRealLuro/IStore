import type { Config } from "tailwindcss";

/** Resolve a CSS-variable color token into a Tailwind-friendly RGB consumer. */
const c = (varName: string) => `rgb(var(${varName}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        page: c("--bg-page"),
        card: c("--bg-card"),
        elevated: c("--bg-elevated"),
        hover: c("--bg-hover"),
        border: c("--bg-border"),
        divider: c("--bg-divider"),

        fg: {
          DEFAULT: c("--fg-primary"),
          secondary: c("--fg-secondary"),
          muted: c("--fg-muted"),
          inverse: c("--fg-inverse"),
        },

        accent: {
          DEFAULT: c("--accent"),
          hover: c("--accent-hover"),
          soft: c("--accent-soft"),
        },
        danger: c("--danger"),
        success: c("--success"),
        warning: c("--warning"),
      },
      boxShadow: {
        soft: "var(--shadow-sm)",
        card: "var(--shadow-md)",
        float: "var(--shadow-lg)",
      },
      borderRadius: {
        "2xl": "16px",
        "3xl": "20px",
        "4xl": "28px",
      },
      animation: {
        "fade-in": "fade-in 200ms ease-out",
        "slide-in": "slide-in 320ms cubic-bezier(0.16, 1, 0.3, 1)",
        "scale-in": "scale-in 220ms cubic-bezier(0.16, 1, 0.3, 1)",
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "slide-in": {
          "0%": { transform: "translateX(100%)", opacity: "0" },
          "100%": { transform: "translateX(0)", opacity: "1" },
        },
        "scale-in": {
          "0%": { transform: "scale(0.97)", opacity: "0" },
          "100%": { transform: "scale(1)", opacity: "1" },
        },
      },
    },
  },
  plugins: [],
} satisfies Config;
