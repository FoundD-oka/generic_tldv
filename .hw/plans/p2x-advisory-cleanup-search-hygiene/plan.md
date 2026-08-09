---
generated_by: fable
task_id: p2x-advisory-cleanup-search-hygiene
base-commit: 05d97b7569db50732d154732c58f7d44de6671dd
size: S
---

# 検索 API まわりの微修正(advisory 11 / 13)— 最後尾・任意

## How

1. `services/meeting-api/meeting_api/search.py`: ルータデコレータの
   `dependencies=[Depends(get_user_and_token)]`(現行 70 行)を削除し、
   パラメータ側 `auth_data: tuple = Depends(get_user_and_token)`(76 行)のみ残す。
   クエリ・認可条件・レスポンスに一切触れない。
2. `services/meeting-api/tests/integration/test_transcript_search_postgres.py`:
   `datetime.utcnow()`(9箇所)を timezone-aware な取得へ置換。**注意**: DB カラムが
   naive(TIMESTAMP WITHOUT TIME ZONE)の場合、aware をそのまま渡すと asyncpg が
   拒否する。モデル定義を確認し、必要なら
   `datetime.now(timezone.utc).replace(tzinfo=None)` 形にする(RF-801。実測で確定)。
3. 検証: fresh venv(python3.11、`~/.cache/hw-venvs/p2x-advisory-cleanup-search-hygiene`、
   admin-models + meeting-api + pytest pytest-asyncio httpx)で unit 全件、
   postgres コンテナで統合テスト。CI(Test Meeting API)は paths に
   `services/meeting-api/**` を含むため PR で自動実走する。

## Why(実装者に渡さない)

- 2件とも挙動不変の衛生修正。価値は py3.12 移行時の警告ノイズ予防と宣言の重複除去
  のみで、本タスク群の中では最小。ユーザーが工数を絞るなら丸ごと落としてよい候補
  として最後尾に置いた。
- 認可は search の生命線(2ユーザーテスト維持が引き継ぎの不変条件)。だからこそ
  「decorator 行削除のみ・認可テスト green 維持」を契約で固定し、ついで修正の
  スコープ拡大を禁止する。
