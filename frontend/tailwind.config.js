/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0d12",
        panel: "#12151c",
        muted: "#9aa3b2",
        accent: "#5b8cff",
        good: "#2ecc71",
        bad: "#e74c3c",
      },
    },
  },
  plugins: [],
};
