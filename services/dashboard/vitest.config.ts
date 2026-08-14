import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  test: {
    // .tsx は React コンポーネントの挙動テスト用。DOM が要るファイルだけ
    // 先頭の `// @vitest-environment jsdom` で個別に環境を切り替える。
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
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
