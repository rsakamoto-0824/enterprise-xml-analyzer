# Enterprise XML Analyzer リリース資材

このフォルダは、社内IIS公開とElectronビルドの準備資材です。

## 主なファイル

- `iis/web.config`: IIS公開用設定
- `iis/app_offline.htm`: 参考用の更新中表示HTML（静的IISのアクセス停止を保証しません）
- `scripts/build-iis-package.sh`: IIS公開用パッケージ作成コマンド
- `electron/main.cjs`: Electron起動用ファイル
- `electron/preload.cjs`: Electron preloadファイル
- `electron/electron-builder.yml`: Electron Builder設定例
- `build-and-publish-steps.html`: ビルド・社内公開手順書

IIS配備資材の正本は `../deployment/` です。ビルドは `artifacts/enterprise-xml-analyzer-iis/` に配布物を生成します。Electronは補助構成で、Windowsビルド・署名・実機動作は未検証です。

## 注意

- APIキー、パスワード、トークンはこのフォルダに書かないでください。
- 既存アプリを修正する場合は、既存の開発環境と公開方法を優先してください。
- 社外公開する場合は、別途定められたインターネット公開ルールを優先してください。
