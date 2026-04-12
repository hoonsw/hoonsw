# hoonsw

AI エージェント、翻訳ワークフロー、自動化ツールを中心に開発している個人ポートフォリオ用モノレポです。

このリポジトリでは、実際に運用・改善しているツールを `projects/` 配下で管理し、ルート README では全体像が分かるように整理しています。

## 現在の公開対象

### `projects/translator`

長文 TXT / PDF 向けの翻訳ワークベンチです。

- 現在の主運用経路: `translator_v3 + codex_local`
- プレビュー生成、レビュー・バンドル、実行メタデータ、Synology 納品、OpenClaw / Discord 運用に対応
- OCR 系入力を前提にした前処理、品質検証、再試行、ジョブ管理を含む

詳細:

- [projects/translator/README.md](projects/translator/README.md)

## 今後追加予定のローカルプロジェクト

現時点では、完成度と公開品質を優先して `translator` のみを収録しています。
今後は必要に応じて以下のローカルプロジェクトを順次移行する予定です。

- `aituber`
- `earEater`
- `coldVision`
- `kindless`
- `BallonsTranslator`
- `myMcp`
- `toMp3`
- `toMp4`

## リポジトリ構成

```text
.
├─ projects/   # 公開対象の実プロジェクト
├─ docs/       # 横断ドキュメント
└─ assets/     # ポートフォリオ用アセット
```

## 運用方針

- README は日本語を基本とする
- コミットメッセージも日本語を基本とする
- 未整理の実験データやローカル生成物は極力含めない
- 公開品質に達したプロジェクトから順に取り込む
