import { defineConfig } from "vite";

export default defineConfig({
  server: {
    host: "127.0.0.1",
    proxy: { "/api": process.env.CODERISK_API_TARGET || "http://127.0.0.1:8000" },
  },
});
