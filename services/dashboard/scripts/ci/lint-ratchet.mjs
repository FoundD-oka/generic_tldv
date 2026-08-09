#!/usr/bin/env node
/**
 * lint-ratchet.mjs — dashboard の既存 lint 負債を凍結し、新規増加だけを止める。
 *
 * 使い方:
 *   npx eslint . --format json --output-file eslint-report.json
 *   node scripts/ci/lint-ratchet.mjs eslint-report.json lint-baseline.json
 *
 * 運用ルール(lint-baseline.json は JSON なのでコメントを書けないためここに置く):
 *   - lint-baseline.json の値は「下げる」ことだけが許される。負債を返したら
 *     実測値まで下げて commit する。
 *   - 値を「上げる」のは禁止。上げれば新規負債が素通りするので、ラチェットが
 *     ラチェットでなくなる。基準値の変更は人間の commit だけで行い、この
 *     スクリプトは baseline を書き換えない(読むだけ)。
 *   - errors だけでなく warnings も凍結する。errors を warnings へ格下げして
 *     通す抜け道を塞ぐため。
 *
 * 依存は Node 標準モジュールのみ。
 */

import { readFileSync } from "node:fs";

function die(message) {
  console.error(`lint-ratchet: ${message}`);
  process.exit(1);
}

function readJson(filePath, label) {
  let raw;
  try {
    raw = readFileSync(filePath, "utf8");
  } catch (err) {
    die(`${label} を読めない (${filePath}): ${err.message}`);
  }
  try {
    return JSON.parse(raw);
  } catch (err) {
    die(`${label} が JSON として不正 (${filePath}): ${err.message}`);
  }
}

const [reportPath, baselinePath] = process.argv.slice(2);
if (!reportPath || !baselinePath) {
  die("usage: node lint-ratchet.mjs <eslint-report.json> <lint-baseline.json>");
}

const report = readJson(reportPath, "eslint report");
if (!Array.isArray(report)) {
  die("eslint report が配列でない (eslint --format json の出力を渡すこと)");
}

const baseline = readJson(baselinePath, "baseline");
for (const key of ["errors", "warnings"]) {
  if (!Number.isInteger(baseline?.[key]) || baseline[key] < 0) {
    die(`baseline.${key} が非負整数でない (${baselinePath})`);
  }
}

let errors = 0;
let warnings = 0;
let fatalErrors = 0;
for (const result of report) {
  errors += result.errorCount ?? 0;
  warnings += result.warningCount ?? 0;
  fatalErrors += result.fatalErrorCount ?? 0;
}

console.log(
  `lint-ratchet: current errors=${errors} warnings=${warnings} fatalErrors=${fatalErrors} / baseline errors=${baseline.errors} warnings=${baseline.warnings}`,
);

if (fatalErrors > 0) {
  die(
    `fatal error が ${fatalErrors} 件ある。パースエラー等は既存負債ではなく故障なので baseline に関係なく fail。`,
  );
}

const over = [];
if (errors > baseline.errors) {
  over.push(`errors ${errors} > baseline ${baseline.errors} (+${errors - baseline.errors})`);
}
if (warnings > baseline.warnings) {
  over.push(
    `warnings ${warnings} > baseline ${baseline.warnings} (+${warnings - baseline.warnings})`,
  );
}
if (over.length > 0) {
  console.error("lint-ratchet: 新規 lint 負債が増えている:");
  for (const line of over) console.error(`  - ${line}`);
  die("この差分で追加された lint 違反を直すこと(baseline を上げて通すのは禁止)。");
}

if (errors < baseline.errors || warnings < baseline.warnings) {
  console.log(
    `lint-ratchet: 負債が減った。lint-baseline.json を {"errors": ${errors}, "warnings": ${warnings}} へ下げて commit すること。`,
  );
}

console.log("lint-ratchet: OK (新規増加なし)");
