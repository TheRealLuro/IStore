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
        // iOS-standard easing — long deceleration tail reads as "smooth"
        // because the eye spends most of the duration on slow movement.
        // Bumped from 320ms → 520ms; perceived smoothness scales with
        // duration up to ~600ms, beyond which it just feels sluggish.
        "fade-in": "fade-in 360ms cubic-bezier(0.32, 0.72, 0, 1)",
        "slide-in": "slide-in 520ms cubic-bezier(0.32, 0.72, 0, 1)",
        "scale-in": "scale-in 280ms cubic-bezier(0.32, 0.72, 0, 1)",
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "slide-in": {
          // Subtle scale + slide combines for a softer arrival than pure
          // off-screen → on-screen. The translateX is enough to read as
          // "from the right" without the bouncy edge slap.
          "0%": {
            transform: "translateX(105%) scale(0.985)",
            opacity: "0",
          },
          "60%": {
            opacity: "1",
          },
          "100%": {
            transform: "translateX(0) scale(1)",
            opacity: "1",
          },
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
