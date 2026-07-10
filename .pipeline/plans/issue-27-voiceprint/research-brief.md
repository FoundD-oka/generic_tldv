# Research Brief — Issue #27 Phase 4: 声紋抽出モデルの選定

日付: 2026-07-10。目的: Phase 4（声紋登録・声紋照合によるクラスタ自動命名）で使う
**話者埋め込み（speaker-embedding）抽出器**を、adapter-contract 化する外部ツールとして
選定するための調査。Soniox には声紋・会議横断本人識別機能がないことは既に確定済み
（`.pipeline/evidence/speaker-attribution-voiceprint/soniox-capability-research.md`）。
承認済み PII 方針（`.pipeline/plans/issue-27-voiceprint/pii-policy-draft.md`）は
「ローカル処理前提・未登録者は照合のみで即時破棄・24ヶ月保持・暗号化」を既に確定している。

## リフレーミング

文字どおりの依頼は「埋め込みモデルを1つ選ぶ」だが、それだけでは実際の意思決定を
支えられない。以下の理由で、決定事項を「モデル選定＋しきい値運用手順」の2点セットに
広げる：

1. ホスト型API（H4）は調査以前から方針上ローカル処理前提のため実質選択肢に入らない
   （調査は「本当に排除できるか」の確認作業になる）。
2. 日本語音声への頑健性（H5）は「モデルを選べば解決する」問題ではなく、
   **しきい値を実測で校正する運用**が必要という結論になりやすい（後述）。これは
   PII 方針が既に定めている「suggest→人間確認→auto」の段階移行方針と整合するため、
   モデル選定の結論は「初期しきい値は保守的に置き、実測してから調整する」という
   既存方針を補強する形になる。

以上を踏まえ、本ブリーフはモデル比較（H1-H4）と、しきい値運用への含意（H5）を
分けて記述する。

## 仮説（調査前に記述）

- **H1**: SpeechBrain ECAPA-TDNN（`speechbrain/spkrec-ecapa-voxceleb`）が2026年時点でも
  セルフホスト・CPU話者照合の実務的デフォルトである — 活発に保守され、ライセンスが
  寛容で、会議後バッチ処理として十分な速度でCPU動作する。
- **H2**: pyannote/embedding（または pyannote community モデル）は同等品質だが、
  ゲーティング/ライセンス摩擦（HFトークン、利用条件同意）が再現可能なデプロイに
  影響する。
- **H3**: WeSpeaker / NVIDIA TitaNet / 3D-Speaker は精度面で有利な代替だが、
  依存が重い（またはGPU志向）で、本デプロイの運用コストに見合わない。
- **H4**: ホスト型話者IDAPI（Azure Speaker Recognition、AWSなど）は退役/制限済み、
  または本デプロイのプライバシー方針と矛盾するため実質除外される。
- **H5**: 日本語音声はx-vector/ECAPA系埋め込みの性能を実質的に劣化させない
  （言語頑健）ため、日本語専用モデルは不要である。

## 検証結果

### H1 — SpeechBrain ECAPA-TDNN: 支持（確信度: 高）

- ライセンス: **Apache-2.0**。ゲーティングなし（HFトークン不要、即ダウンロード可）。
  （出典: https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb, 2026-07 取得）
- 保守状況: 2026年に入っても `speechbrain/speechbrain` 本体リポジトリはコミット
  活動が継続（2026-05-03更新確認）、関連リポジトリ（benchmarks, HyperPyYAML）も
  2026年に更新あり。モデル自体のダウンロード数は月間244万件、328 likes と
  実運用での採用実績が大きい。
  （出典: GitHub `speechbrain/speechbrain` activity, HF model page, 2026-07 取得）
- CPU性能: リアルタイム話者ダイアリゼーションでECAPA-TDNNを埋め込み抽出に使う
  参照実装が「CPUのみでRTF<0.1、定常レイテンシ約5.5秒」という報告あり
  （本タスクは会議後バッチ処理なのでリアルタイム制約はさらに緩い）。
  （出典: Springer JASMP 2024 論文, arXiv 2506.19875, 2026-07 取得検索）
- 精度: VoxCeleb1テストでEER 0.80–0.90%（s-norm有無）。192次元埋め込み、
  コサイン距離でスコアリング。
- **反証探索の結果（H1を弱める材料）**: 同じ調査で **CAM++**（3D-Speaker/ModelScope,
  Apache-2.0）がECAPA-TDNNの約半分のパラメータ数・FLOPsで2倍以上の推論速度、
  同等以上の精度という報告を確認した（arXiv 2303.00332 他）。これはH1の
  「実務的デフォルト」という位置づけそのものを覆すものではないが、**CPUレイテンシが
  ボトルネックになった場合の妥当な代替**として無視できない。ただしCAM++は
  SpeechBrainほど成熟した英語ドキュメント・単一パッケージ導入体験を持たず、
  ModelScope系ツールチェーンへの依存が増える点で運用コストはSpeechBrainより高い。
  → 結論は変えず、フォールバックとして記録（option-matrix参照）。
- モデルカードの明記事項: 「SpeechBrainチームはVoxCeleb以外のデータセットでの
  性能について保証しない」との免責記載あり。日本語会議音声はこの「保証外」領域に
  該当する（H5と関連）。

### H2 — pyannote/embedding: 部分的に支持（確信度: 高＝摩擦の存在、中＝実害の大きさ）

- ライセンス: **MIT**（技術的には商用利用も許可）。
- ゲーティング: 確認された。HuggingFace上で「利用条件への同意」が必須で、
  アクセストークンを発行してモデルロード時に渡す必要がある
  （出典: https://huggingface.co/pyannote/embedding, 2026-07 取得）。
- 商用誘導: モデルカード自体が「本番で使うなら pyannoteAI（商用サービス）への
  切り替えを検討してほしい」と明記し、企業ユーザーには寄付や商用相談を促す文言がある。
  ライセンス上のブロッカーではないが、**運用上の摩擦（トークン管理、HF承認フローへの
  依存、ベンダーからの商用誘導）は仮説どおり実在する**。
- 保守状況: pyannote.audio 2.1系列として現在も配布されているが、開発リソースは
  pyannoteAI（商用）側に重心が移っている兆候がある（`community-1`/`precision-2`という
  ティア分けの登場）。
- 判定: H2は支持。SpeechBrainがゼロ摩擦（トークン不要・単一Apache-2.0）である一方、
  pyannoteは技術的に使えるが小さな運用コストが常に乗る。単一テナント・小規模運用の
  本デプロイでは、この差が意思決定を動かすほどではないにせよ、SpeechBrainを選ぶ
  積極的な理由になる。

### H3 — WeSpeaker / TitaNet / 3D-Speaker: 部分的に支持、要修正（確信度: 中）

- **NVIDIA TitaNet（NeMo）**: ライセンスは**CC-BY-4.0**。推論自体はONNX変換で
  CPU実行が可能だが、NeMoフレームワーク全体は Apex / Megatron Core /
  Transformer Engine など重い依存を要求し、NVIDIA自身がコンテナ利用を推奨する
  ほど導入が煩雑（出典: NVIDIA NeMo公式ドキュメント, 2026-07 取得）。
  → **仮説どおり、GPU志向・運用コスト高で本デプロイには不向き**（支持）。
- **WeSpeaker**: ライセンス**Apache-2.0**。CPU/GPU両対応、ONNX/JITエクスポート対応で
  「研究・実運用向け」を明確に志向したツールキット。GPUはクラスタリング処理で
  CPU比約3倍高速化との報告があるが、埋め込み抽出自体はCPUでも動作する。
  SpeechBrainより導入の手間はあるが「使えない」ほどの重さではない。
- **3D-Speaker（Alibaba/ModelScope）**: ライセンス**Apache-2.0**。CAM++を含み、
  H1のセクションで述べた通り軽量・高速。
- **反証**: 「精度面で有利だが運用コストに見合わない」という当初仮説は
  TitaNet/NeMoには当たるが、WeSpeaker・3D-SpeakerのCAM++には当てはまらない
  ——これらは**軽量かつCPUフレンドリー**であり、単に「SpeechBrainほど枯れた
  単一パッケージ体験ではない」という程度の差。H3はTitaNetについては支持、
  WeSpeaker/3D-Speakerについては仮説を修正（除外ではなくフォールバック候補）。

### H4 — ホスト型話者IDAPI: 支持（確信度: 高）

- **Azure AI Speaker Recognition**: **2025年9月30日付で退役（提供終了）確定**。
  以後APIアクセス不可。Microsoft公式が pyannote/SpeechBrain等のOSSをリアルタイム
  ダイアリゼーションの代替として案内。
  （出典: Microsoft Q&A / Azalio retirement notice / picovoice blog, 2026-07 取得）
- **AWS**: 単独の話者識別APIは現在も存在しない。Amazon Transcribeのダイアリゼーションは
  匿名ラベル（Speaker 0, 1…）のみで本人識別・会議横断照合はできないと明記。
  さらに Amazon Connect Voice ID（話者照合機能）自体が**2026年5月20日でサポート終了**
  予定であることを確認 — ホスト型話者IDはAWS内でも縮小方向。
  （出典: AWS公式ドキュメント, 2026-07 取得）
- サードパーティ（AssemblyAI等）はAWS Marketplace経由で話者識別を提供するが、
  音声を第三者（サードパーティAPI事業者）に送信する構成になり、承認済みPII方針の
  「ローカル処理前提」と矛盾する。
- 判定: H4は完全に支持。退役/縮小という事実面と、方針上の排除の両方が確認できた。

### H5 — 日本語音声への頑健性: 支持されない・要修正（確信度: 中〜高、重要な発見）

これが最も重要な反証結果である。

- 「言語ミスマッチはクロスリンガル話者照合の性能劣化の主因である」という一次文献が
  複数存在する（enrollment言語とtest言語が異なるトライアルでの劣化）。本デプロイは
  enrollment・照合とも日本語音声で統一されるため、この「言語ミスマッチ型」の劣化は
  厳密には当てはまらない可能性がある。
- しかし、**訓練データ言語と運用言語の不一致（英語VoxCelebで学習→日本語会議音声で運用）**
  という、より直接的に関係するシナリオでの証拠は以下の通り: VoxCeleb（英語・クリーンな
  celebrity interview音声）でEER 0.8-0.9%を達成するECAPA-TDNN系モデルが、
  CN-Celeb（中国語・ドラマ/歌唱/スピーチ等の多様なジャンルを含む挑戦的コーパス）では
  EER 8.8%程度まで悪化するという報告がある（同一アーキテクチャ・訓練/評価は
  それぞれのコーパス内で一致条件、を含む）。これは言語差だけでなく録音条件の
  多様性・難易度差の影響も大きいと考えられるため、「言語」単体の効果を分離できていない
  点は留保が必要。
- 日本語音声そのものを対象にしたECAPA-TDNN/x-vectorのEERベンチマークは、
  今回の調査では**発見できなかった**（証拠の空白）。
- 判定: 「日本語だから劣化しない」と楽観視する根拠は見つからなかった。むしろ
  「クリーンな英語VoxCelebベンチマーク（EER<1%）をそのまま日本語会議音声に
  適用できると仮定するのは危険」という保守的な結論が妥当。会議音声は録音条件も
  VoxCelebより悪い（マイク差、部屋の反響、複数人発話区間の切り出し誤差を含む
  クラスタ単位の埋め込みなど）ため、劣化要因は言語以外にも複数重なる。
- **この発見はPII方針と整合し、方針をむしろ補強する**: 承認済みPII方針は
  「初期しきい値は保守的に置き、誤マッチ率を実測してから調整する」「初期リリースは
  suggest→人間確認必須」と既に定めている。今回の調査結果は、この慎重な運用方針が
  単なる法務上の予防線ではなく、**技術的にも正当化される**ことを示している。

## 結論

- **推奨**: SpeechBrain ECAPA-TDNN (`speechbrain/spkrec-ecapa-voxceleb`) を
  Phase 4 の一次埋め込み抽出アダプタとして採用する。
- **確信度**: 高（モデル選定そのものについて）。中（日本語会議音声での実際の
  誤マッチ率が事前にどの程度かについては低〜中 — これは選定の問題ではなく、
  実測が必要な未知数）。
- **この結論を覆す条件（overturning conditions）**:
  1. SpeechBrainリポジトリが保守停止、またはApache-2.0からの再ライセンスなど
     ライセンス条件が悪化した場合。
  2. PII方針が既定している実測フェーズ（suggest→人間確認ログ）で、日本語会議の
     クラスタ埋め込みに対しコサイン0.70-0.75しきい値が高い誤マッチ率/誤棄却率を
     示した場合 — その場合はしきい値変更に加え、CAM++（3D-Speaker/WeSpeaker）への
     切り替えまたは両モデルの併用検証が必要になる。
  3. 将来、処理量がCPUバッチの限界を超える規模になった場合（GPU導入が前提になるなら
     TitaNet等の再評価余地が生まれる）。
  4. 日本語音声に特化したオープンな話者埋め込みモデル（ESPnet系やReazonSpeech関連の
     コミュニティ成果など）が今後登場し、ローカルでの検証によりECAPA-TDNNより
     明確に優れると確認された場合。
  5. pyannoteのゲーティングポリシーが撤廃され、SpeechBrainと同等の摩擦ゼロになった
     場合（H2の判定根拠が弱まるのみで、推奨自体への影響は小さい）。

## 出典一覧（取得日: 2026-07-10）

- https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb
- https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb/discussions/7
- https://github.com/speechbrain/speechbrain（activity）
- https://huggingface.co/pyannote/embedding
- https://github.com/pyannote/pyannote-audio, LICENSE
- https://huggingface.co/nvidia/speakerverification_en_titanet_large
- https://docs.nvidia.com/nemo-framework/user-guide/latest/（installation, requirements）
- https://github.com/wenet-e2e/wespeaker
- https://github.com/modelscope/3D-Speaker
- arXiv 2303.00332（CAM++）
- arXiv 2005.07143（ECAPA-TDNN原論文）
- arXiv 2104.01466（ECAPA-TDNN for diarization）
- Springer JASMP 2024（リアルタイムダイアリゼーションのRTF報告）
- CN-Celeb関連文献（クロスリンガル劣化のEER比較）
- Microsoft Azure Speaker Recognition retirement notice（Azalio, picovoice blog, Microsoft Q&A）
- AWS公式ドキュメント（Amazon Transcribe, Amazon Connect Voice ID EOL）
