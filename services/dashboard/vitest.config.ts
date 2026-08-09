import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  test: {
    include: ["tests/**/*.test.ts"],
    // テストプロセスのタイムゾーンを固定する。日付整形を伴うテストが
    // 実行環境(ローカル / CI ランナー)の TZ に暗黙依存しないようにするため。
    env: {
      TZ: "Asia/Tokyo",
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
});
