# Verification Contract — fix-dashboard-auth-redirect-loop

対象差分: `db823063f083bc66cfb6f95ac1c7e3bd9d92298c..HEAD`
証跡ルート: `.hw/plans/fix-dashboard-auth-redirect-loop/evidence/`

## Acceptance Tests

| ID | Requirement(最低合格ライン) | Method | Evidence |
|---|---|---|---|
| AT-001 | 旧形式 `vexa-auth`(`isAuthenticated:true`)をseedしたrehydrate後、サーバー検証前は `isAuthenticated===false`。persist出力に `isAuthenticated`/`token` を含まない | unit (vitest) | `evidence/test-output.txt` |
| AT-002 | legacyキー6種がlocalStorage/sessionStorageから消える | unit (vitest) | 同上 |
| AT-003 | `/api/auth/me` 401は全クリア+`unauthorized`、fetch rejectは非認証+`network`で、認証済みへフォールバックしない | unit (vitest) | 同上 |
| AT-004 | login page: shared-login 200で `/meetings` へのreplaceがちょうど1回 | component (vitest) | 同上 |
| AT-005 | shared-login 500/network rejectでredirect 0回、エラーUI+再試行。再試行成功時だけ遷移 | component (vitest) | 同上 |
| AT-006 | AuthProvider: checkAuth 401+shared-login失敗で `/login` pushが1回。network時はpush 0回でエラーUI | component (vitest) | 同上 |
| AT-007 | shared-login成功API contractがuser/tokenを含む | integration (vitest) | 同上 |
| AT-008 | dashboardの `npm test` 全緑、`npm run build` 成功 | command | `evidence/test-output.txt` |
| AT-009 | `.hw/verify.sh` でbaseline外の新規失敗ゼロ | command | `evidence/verify-output.txt` |
| AT-010 | 一意タグでbuildし、再作成はdashboard/kabosu-dashboardのみ。他コンテナ、`.env` hash、volume一覧は不変 | command | `evidence/deploy-snapshots/` |
| AT-011 | 3001/3002 health=200、sharedAuthはfalse/true、3002 shared-login=200 | command | `evidence/health-checks.txt` |
| AT-012 | URLサンプリング3ケース: 新規は最大1遷移、legacyは往復0で終端、shared-login 500はredirect 0+エラーUI+再試行 | browser | `evidence/url-sampling-*.log`, screenshots |
| AT-013 | cookie付き3002 meetings proxyが200でmeeting JSONを返す | command | `evidence/health-checks.txt` |
| AT-101 | 編集前impact、commit前detect_changesを実行。不能時はGrep裏取りを概算と明記 | command/source | `evidence/impact.txt`, `evidence/detect_changes.txt` |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | shared auth成功時の自動ログインを維持 | component + browser | AT-004/AT-012 |
| FP-002 | テスト削除・skip・期待値緩和なし | diff audit | `evidence/test-diff-audit.txt` |
| FP-003 | `make -C deploy/compose up` 未実行。更新は安全フラグ付き2サービス限定 | command audit | `evidence/deploy-snapshots/commands.txt` |
| FP-004 | `.env`、volume、他サービス不変 | snapshots | AT-010 |
| FP-005 | 3001通常Dashboardのlogin表示を維持 | browser/curl | `evidence/health-checks.txt` |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | 失敗時は日本語の明示エラー+再試行で、無限スピナーにしない | component/browser | screenshots |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes
- hash-bound approval required: yes
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-001 | zustand persistのversion/migrate挙動 | installed version/source + AT-001 test | `evidence/test-output.txt` |
