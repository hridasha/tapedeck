/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0d12",
        panel: "#141821",
        line: "#262d3d",
        muted: "#8b94a8",
        accent: "#7c8cff",
        ok: "#3ecf8e",
        bad: "#ff6b62",
        warn: "#f5a524",
      },
      fontFamily: { mono: ["Consolas", "ui-monospace", "monospace"] },
    },
  },
  plugins: [],
};
