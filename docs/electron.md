# Electron補助構成

IISを主配布方式とします。Electronは社内共通ルールに基づく補助資材です。Windowsでビルドする場合、開発端末に固定バージョンを追加します。

```powershell
npm install --save-dev --save-exact electron@44.9.0 electron-builder@26.15.3
npm run electron:build
```

ビルド時にElectronバイナリを取得するためインターネットまたは承認済みの社内キャッシュが必要です。利用者の実行時には外部通信を必要としない設計です。配布前にWindows実機でWorker・ストレージ・保存・署名を確認してください。インストーラー生成・Windows実行・署名検証は未実施です。
