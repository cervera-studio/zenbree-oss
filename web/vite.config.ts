import path from 'node:path';
import { defineConfig } from 'vite-plus';

// The web container runs in UTC; set before workers spawn so they inherit it and a test cannot
// pass only because the laptop sits in the user's zone.
process.env.TZ = 'UTC';

export default defineConfig({
  resolve: { alias: { '@': path.resolve(__dirname) } },
  test: { include: ['**/*.test.{ts,tsx}'], exclude: ['node_modules', '.next'] },
});
