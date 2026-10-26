# Windows版ユーザー手順書

`procedure-guide.html` をPCのブラウザで開いてください。このフォルダ内のCSS・JavaScript・画像・練習用XMLをセットで保管します。インターネット接続は不要です。画像をクリックすると拡大できます。

Windows実行版にはこのフォルダを `UserGuide/` として同梱します。手順書だけを配布する場合は、リポジトリのルートで `python scripts/package_user_guide.py` を実行します。

- `artifacts/enterprise-xml-analyzer-user-guide.html`: 画像・スタイル・操作・練習用XMLの保存リンクをすべて含むHTML単体版
- `artifacts/enterprise-xml-analyzer-user-guide.zip`: このフォルダをまとめたZIP。展開後に `procedure-guide.html` を開く

## 画面と検証

実画面11枚は[Windows CI](https://github.com/rsakamoto-0824/enterprise-xml-analyzer/actions/runs/40413316950)で撮影しました。架空の商品3件だけを使用し、撮影後の画像加工はしていません。撮影環境と比較件数は `images/capture.json` に記録しています。XLSX保存はファイル選択ダイアログだけを自動指定し、アプリの実際の出力処理を実行しています。

手順書はPC幅1440pxと1280pxのChromiumで確認します。HTMLの画像11枚、章目次12箇所、画像拡大とEscでの復帰、練習用XMLのダウンロード、横方向のはみ出し、HTML単体版の外部資材依存を確認します。

再撮影はWindows x64・Python 3.14.6で `python scripts/capture_guide_python.py` を実行します。出力先は `artifacts/python-guide/` です。手順書へ採用する画像と撮影記録を `images/` にコピーし、本文と画面の一致を確認してください。
