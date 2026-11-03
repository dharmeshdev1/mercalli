import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@/": fileURLToPath(new URL("./src/", import.meta.url)),
      "@fixtures/": fileURLToPath(new URL("./fixtures/", import.meta.url)),
    },
  },
  test: {
    include: ["src/**/*.test.ts", "fixtures/**/*.test.ts"],
    environment: "node",
  },
});
