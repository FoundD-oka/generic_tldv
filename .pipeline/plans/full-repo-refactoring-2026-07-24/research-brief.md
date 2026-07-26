# Research Brief: 全リポジトリ・リファクタリング

## 問いの言い換え

元の問いは「巨大ファイルや重複をどう分割するか」ではない。実際に解くべき問いは、
「現在の外部契約と状態遷移を先に固定し、既知の境界バグを別ファイルへ温存せず、
検証基盤の偽陽性を除いたうえで、責務を段階的に移動するにはどの順番が安全か」である。

## 事前仮説

1. 最大のリスクはファイル長ではなく、認証主体、会議ライフサイクル、非同期状態、
   Pub/Sub応答の所有者が複数箇所へ分散していること。
2. 既存テスト数は多いが、ルート検証基盤には「未実行でも成功」に見える経路があり、
   全体の安全網としてはそのまま信用できない。
3. `MANIFEST.md` のモジュール化方針は参考になるが、別ブランチを前提とする将来仕様を
   現在の `main` へそのまま適用すると、現行サービスとデプロイ契約を壊す。

## 反証確認

- Dashboard 28ファイル・199テスト、`transcript-rendering` 83テスト、
  Vexa Bot型検査は成功しており、「テストが存在しない」は誤りだった。
- 一方、Dashboard lintは既存61 errors / 87 warnings、`tests3` は91登録中45スクリプトが
  不在、0 report / 0 featureでもgate成功可能であり、「既存gateが全体を証明する」は
  反証された。
- 起動済みカボスDashboardを `http://127.0.0.1:3002` で読み取り確認できたが、
  イメージは10日前で現HEADと同一ではない。実画面は補助証拠であり、HEADの静的確認と
  テストを置き換えない。
- `MANIFEST.md` は inventory を `feat/extension-in-tab-capture` 基準と明記し、
  statusも「merge後にbinding」である。現行 `main` の確定仕様としては扱わない。

## 現在の一次資料

- 基準コード: `main` / `b2bcae8e88f0e73fe95343ee3a694a3afc4e1028`
- リポジトリ内:
  - `AGENTS.md`
  - `docs/managed-agent-harness-architecture.md`
  - `docs/agent-coding-best-practices.md`
  - `services/README.md`
  - `MANIFEST.md`
  - `tests3/README.md`
  - 実装、テスト、Compose、CI、Harnessスクリプト
- Redis公式: [Pub/Subはat-most-onceで、購読中でない受信者はメッセージを失う](https://redis.io/docs/latest/develop/use-cases/pub-sub/)
- OWASP公式:
  - [API1 Broken Object Level Authorization](https://owasp.org/API-Security/editions/2019/en/0xa1-broken-object-level-authorization/)
  - [API3 Broken Object Property Level Authorization](https://owasp.org/API-Security/editions/2023/en/0xa3-broken-object-property-level-authorization/)
- FastAPI公式:
  - [`APIKeyHeader(auto_error=False)` はヘッダー欠落時に `None` を返す](https://fastapi.tiangolo.com/ja/reference/security/)

## 確定した示唆

1. `user_id` をquery/bodyから採用する経路は、Gatewayが注入する認証主体へ統一する。
2. 公開レスポンスは汎用JSONを後からdeny-listするのでなく、公開可能フィールドを
   allow-listする。
3. Browser保存をRedis Pub/Subの順序工夫だけで直しても、生成containerへglobal Redis
   credentialを残す越権問題は解消しない。Meeting serviceのsession-bound WS brokerへ
   移し、waiter登録後に送信して`request_id`を照合する。永続再実行保証は追加しない。
4. 先に振る舞いとside-effect順をgolden/特性テスト化し、その後にmove-only抽出を行う。
5. `tests3` は即時全面廃止も全面復元もせず、偽陽性をfail-closed化し、現行Managed
   Harnessとサービス別テストへ権威を明示する。
6. `INTERNAL_API_SECRET`やAdmin keyを用途名だけ変えて再利用すると、Bot/Browser/Meeting
   の1侵害が別audienceへ波及する。Agent config、Gateway identity、Runtime、MeetingToken、
   callback、storage、event、Transcription、Wake、Recordingを別credential/audienceへ分ける。
   Agent provider credentialは認証subject所有に限定し、Zoom SDK client secretとupstream
   proxy user-infoは生成workloadへ配布せず、server-side issuer/brokerへ閉じ込める。
7. unsigned admin cookie、未署名user-info email、service-key fallbackの三つは、いずれも
   browser-facing BFFが強いserver credentialの代理人になるconfused-deputy問題である。
   DTO redactionより先に認証subjectと署名検証を固定する。

## 信頼度と覆る条件

- 信頼度: 高（現行コード、テスト実行、import/AST解析、起動済み画面、公式一次資料で確認）。
- 次の場合は計画を止めて再調査する:
  - 実行開始時のHEADが基準SHAから変わり、対象シンボルまたは公開契約が変更済み。
  - `MANIFEST.md` の前提ブランチが `main` へ正式統合され、契約/gateが実装済み。
  - Agent APIのshipping方針またはtoken scope仕様が別の承認済み文書で確定。
  - 本番でBrowser保存に永続保証が必要と判明し、session WSのbounded retryなし契約が要件を満たさない。

## 時間で陳腐化する前提

- 起動済みDockerイメージの年齢、テスト成功数、lint件数、GitNexus index freshness。
- これらは実行項目0で必ず再計測する。
