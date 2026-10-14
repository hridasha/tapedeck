import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the page talks to a Tapedeck started with `tapedeck ui --no-open`.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    proxy: {
      "/api": "http://localhost:8700",
      "/ws": { target: "ws://localhost:8700", ws: true },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
