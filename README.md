# Enterprise XML Analyzer

XMLをブラウザ内で分割解析し、構造表示・比較・項目抽出・Excel/CSV出力を行う日本語の静的Webアプリです。IISには配信ファイルだけを置き、XMLはアップロードしません。現在の制約と確認範囲は [既知の制限](docs/limitations.md) を参照してください。

## 開発・ビルド

Node.js 24 LTSとnpmを使用します。開発時に依存パッケージを取得し、成果物にはすべてを同梱します。実運用のサーバー・利用者PCにNode.jsは不要です。

```sh
npm ci
npm test
npm run build
npm run dev
```

ビルド結果は `dist/`、配布一式は `artifacts/enterprise-xml-analyzer-iis/` と同名ZIPです。WindowsでのZIP作成には `Compress-Archive -Path artifacts/enterprise-xml-analyzer-iis/* -DestinationPath artifacts/enterprise-xml-analyzer-iis.zip` を使用できます。環境変数・秘密情報の設定はありません。`npm run dev` は開発専用です。

## 使い方

1. 「XML読込」で1～16ファイルを選択、またはドロップします。
2. 「構造ビュー」で要素を選択します。属性・テキスト・パスを右ペインで確認できます。
3. 「この要素を出力の1行にする」で繰り返し要素の列候補を作成します。
4. 出力する列のチェック、型、列名、繰り返し方法を指定し、プレビュー後に保存します。
5. 2つ以上のXMLがある場合、「XML比較」でA/Bを選択します。一意キーがない繰り返し要素は曖昧として表示する場合があります。

名前空間を持つパスは `{URIをパーセントエンコード}localName` で識別します。画面で選択したパスを使用してください。一般的なXPathの式・述語・関数全般は実装していません。

## IIS配布

1. ZIPを管理用フォルダで展開します。
2. 専用の空フォルダを公開先に作成し、IISでHTTPSと認証・アクセス制御を設定します。
3. `deployment/deploy.ps1` に `wwwroot`、公開先、公開外のバックアップ先を指定します。まず `-DryRun` で確認します。
4. `deployment/verify-deployment.ps1` を実行し、Edgeで受入テストします。

サイト直下と `/xmlviewer/` の両方に対応します。URLは末尾 `/` を付けます。配布対象は **wwwrootの内容だけ** であり、ソースや管理用スクリプトを公開しません。詳細: [IIS導入・更新・復旧](docs/IIS-deployment.md)、[HTML操作手順](release/build-and-publish-steps.html)。

## テスト

```sh
npm test
npm run build
npm run test:e2e
npm run verify:package
npm run test:performance
```

E2Eはインストール済みのMicrosoft Edgeを既定で使用します。Chromeは `BROWSER_CHANNEL=chrome npm run test:e2e`（PowerShellは `$env:BROWSER_CHANNEL='chrome'`）で選べます。実測結果は [性能報告](docs/performance-report.md)、対象と未検証項目は [テスト報告](docs/test-report.md) を参照してください。

## 主な構成と依存

- `src/parser` / `workers`: Saxes 6.0.0による分割解析、Worker処理
- `src/storage`: idb 8.0.4を使用したIndexedDB保存
- `src/diff`: SHA-256（@noble/hashes 2.4.0）と親スコープ内のノード照合
- `src/export`: ExcelJS 4.4.0と逐次CSV生成
- `src/components` / `visualization`: 日本語UI、仮想リスト、D3 7.9.0グラフ
- `deployment`: IIS設定とPowerShell配布・復旧・確認スクリプト
- `tests`: Vitest 5.0.5 / Playwright 1.64.1
- Vite 8.3.4 / TypeScript 7.0.2。直接依存と修正版uuid 11.1.1を固定し、lockfileで間接依存も固定。

XLSX生成は全ブックをメモリに持つため、5万行・50万セル・1600万文字で停止します。大量データにはHTTPSのEdgeでCSV逐次保存を使用してください。XML読み込みの100MB対応と、無制限のExcel出力は別です。

Electronは社内共通ルールに基づく補助構成です。IISが主たる配布方式です。[Electron手順](docs/electron.md)を参照してください。

主な残課題は [issues.md](issues.md) で管理しています。Windows実機の受入確認が終わるまでは、合成データでの評価用として扱ってください。
