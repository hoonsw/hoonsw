# Translator

長文 TXT / PDF 原稿向けの翻訳ワークベンチです。

OCR 由来のノイズを含む原稿や、書籍クラスの長文入力を前提に、翻訳、検証、再試行、ジョブ管理、納品までを一連の流れとして扱えるように設計しています。

## 概要

- 主運用経路: `translator_v3 + codex_local`
- 互換・比較用経路: `translator_v2`
- ローカル運用補助: `job_runner.py`
- 通知・実行ラッパ: `scripts/run_job_runner_codex.sh`

## 主な特徴

- TXT / PDF 入力への対応
- OCR 系入力を考慮した分類と正規化
- プレビュー生成
- レビュー・バンドル生成
- 品質検証と再試行
- Synology 納品と共有リンク生成
- OpenClaw / Discord 連携

## 現在の実装上の位置づけ

- `translator_v3` が現在の実運用ベースです
- `translator_v2` は互換・比較・デバッグ用途として保持しています
- 外部 extractor は現時点では `docling` が主系統です
- OCR / 文書理解まわりは今後も改善余地があります

## ディレクトリの考え方

公開リポジトリではコードと文書を中心に管理し、実行時データは含めません。

ローカル運用では以下のような作業ディレクトリを使います。

- `source/` : 原文
- `translated/` : 翻訳結果
- `jobs/` : 実行状態、ログ、結果メタデータ
- `delivery/` : 納品先

## 最低限の使い方

### ローカル実行

```bash
cd projects/translator
./scripts/run_job_runner_codex.sh source/reco.txt
```

出力先を明示する場合:

```bash
cd projects/translator
./scripts/run_job_runner_codex.sh source/reco.txt translated/reco_ko.txt
```

プリセットを付ける場合:

```bash
cd projects/translator
./scripts/run_job_runner_codex.sh source/reco.txt translated/reco_ko.txt --preset en_nonfiction
```

### 直接 CLI を使う場合

```bash
python3 job_runner.py input.txt -o output.txt --v3 --v3-backend codex_local
```

## 補足

- ChatGPT Pro / Codex を使うローカル運用を前提にしています
- API ベースの経路も残っていますが、現状の主運用は Codex ローカル実行です
- 実際の運用では長時間ジョブ、プレビュー確認、レビュー保留 (`blocked`) を前提に扱います

## 関連ドキュメント

- [docs/translator_v3/README.md](docs/translator_v3/README.md)
- [docs/translator_v3/architecture.md](docs/translator_v3/architecture.md)
- [docs/translator_v3/phase-plan.md](docs/translator_v3/phase-plan.md)
- [docs/translator_v2/README.md](docs/translator_v2/README.md)
