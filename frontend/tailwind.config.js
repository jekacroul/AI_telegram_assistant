/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["DM Sans", "system-ui", "-apple-system", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      fontSize: {
        "2xs": ["11px", "16px"],
      },
      colors: {
        dark: {
          bg: "#0f1117",
          card: "#161b27",
          card2: "#1e2535",
          hover: "#252d3d",
          border: "#2a3347",
          border2: "#374159",
        },
        light: {
          bg: "#f0f2f7",
          card: "#ffffff",
          card2: "#f8fafc",
          hover: "#e8ecf5",
          border: "#dde3ef",
          border2: "#c8d0e0",
        },
        bg: "rgb(var(--bg) / <alpha-value>)",
        panel: "rgb(var(--panel) / <alpha-value>)",
        surface: "rgb(var(--surface) / <alpha-value>)",
        sidebar: "rgb(var(--sidebar) / <alpha-value>)",
        fg: "rgb(var(--fg) / <alpha-value>)",
        muted: "rgb(var(--muted) / <alpha-value>)",
        line: "rgb(var(--line) / <alpha-value>)",
        accent: "rgb(var(--accent) / <alpha-value>)",
        "accent-fg": "rgb(var(--accent-fg) / <alpha-value>)",
        good: "rgb(var(--good) / <alpha-value>)",
        bad: "rgb(var(--bad) / <alpha-value>)",
      },
      animation: {
        "count-up": "countUp .4s ease forwards",
        "fade-in": "fadeIn .25s ease forwards",
        "slide-up": "slideUp .3s ease forwards",
        "pulse-dot": "pulseDot 1.5s ease infinite",
        shimmer: "shimmer 1.5s ease infinite",
        expand: "expandDown .25s ease forwards",
      },
      keyframes: {
        countUp: {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "none" },
        },
        fadeIn: { from: { opacity: "0" }, to: { opacity: "1" } },
        slideUp: {
          from: { opacity: "0", transform: "translateY(10px)" },
          to: { opacity: "1", transform: "none" },
        },
        pulseDot: {
          "0%,100%": { opacity: "1" },
          "50%": { opacity: ".3" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        expandDown: {
          from: { opacity: "0", transform: "scaleY(.95)", transformOrigin: "top" },
          to: { opacity: "1", transform: "scaleY(1)" },
        },
      },
    },
  },
  plugins: [],
};
