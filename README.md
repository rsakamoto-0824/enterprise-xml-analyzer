# Enterprise XML Analyzer — Pythonスタンドアローン版

Windowsの実行環境の違いに対応するため、主なアプリをPython 3.14.6のデスクトップ版へ作り直しました。XMLの階層表示・グラフ・比較・項目抽出・XLSX/CSV出力を、PC内で実行します。

**実行時の追加pipパッケージは0件です。** Windows配布版はPython 3.14.6本体とTcl/Tk等を同梱し、ブラウザ、Node.js、IIS、インターネット接続を必要としません。

## Windowsで起動

Windows x64用の `enterprise-xml-analyzer-windows-x64-python3146.zip` をすべて展開し、`EnterpriseXmlAnalyzer/EnterpriseXmlAnalyzer.exe` を起動します。`_internal` フォルダを削除・移動しないでください。

ソース版は、CPython **3.14.6完全版（Tcl/Tk付き）** で起動します。別のバージョンでは起動を止めます。

```bat
py -3.14 run.py
```

または `start_windows.cmd` をダブルクリックします。Pythonのembeddableパッケージは通常Tkinterを含まないためソース版の実行には使いません。詳しい手順は [Windows操作・配布ガイド](docs/python-windows.md) を参照してください。

## 使い方

### 簡単出力モード（回帰・分類向け、v2.2）

XMLを読み込み、「Excel出力」で **構造を確認・プレビュー** を押します。全XMLで共通する最も浅い繰り返し要素を自動候補とし、利用者が1行の単位を変更できます。必要なら予測したい項目を「目的変数」に選び、**学習データをExcel保存** を押します。XMLファイル単位（1ファイル＝1行）にも切り替えられます。

- **学習データ**：選んだ要素1件を1行、配下の値・属性を1項目1列に展開。子要素の繰り返しは `[1]`、`[2]` の別列です。
- **出典**：ファイル名とデータ連番の対応。ファイル名・内部ID・パスを学習列へ自動追加しません。
- **項目定義**：全件で判定した型、目的変数、ID候補、欠損数、定数列候補、XML相対パス。
- **出力情報**：1行の単位、件数、欠損・型の扱い、学習前の確認事項。

数値・真偽値は対応するセル型で出力し、ID候補・先頭ゼロ・15桁超の数値は文字列として保持します。欠落・空文字・空白だけの値は空欄です。全件で数値と文字列が混在する列は文字列に統一します。自動補完・行削除・モデル学習は行いません。目的変数の指定は項目定義に記録され、列の削除や符号化は行いません。学習前にID・定数列・欠損・同一対象や時系列のデータ漏洩を確認してください。

選んだ要素の外側の値は含みません。必要なら行の単位を親へ変更します。1件1024列・文字量4Mi UTF-16単位、1セル32767 UTF-16単位が上限です。超過時は行を分割せず保存を中止します。シートは1048575データ行で分かれ、補助シートを含む合計200万行が安全上限です。

従来の親・空要素を含む一覧は、**全要素一覧をExcel保存** で利用できます。こちらは1要素1行・11列で、長い値を継続行へ分割します。両モードとも読み込み済みの全XMLが対象で、詳細出力の列・条件・対象選択を変更しません。

### ウインドウのサイズ変更

最小640×480に対応します。幅が狭い場合は上部の「画面」で7画面を切り替えます。ツールバーは折り返し、各画面の縦スクロールと表・詳細設定の横スクロールで操作できます。入力内容と読み込み済みXMLはサイズ変更後も保持します。

### 詳細出力・比較

[スクリーンショット付きHTML操作手順書](docs/python-user-guide/procedure-guide.html) を用意しています。Windows配布版では `UserGuide/procedure-guide.html` をブラウザで開いてください。練習用XMLも同梱しています。HTML単体版と手順書ZIPは `python scripts/package_user_guide.py` で作成できます。

1. 「XML読込」でXMLを選択、またはウインドウへドロップします。
2. 「構造ビュー」で繰り返し要素を選び、「この要素を出力の1行にする」を押します。
3. 「Excel出力」で列を選び、型・繰り返し・条件を設定します。列の編集後は「列へ反映」で確定します。
4. プレビュー後にXLSXまたはCSVを保存します。複数XMLの統合・別シート・別ファイルにも対応します。
5. 「XML比較」でA/Bを選び、構造・値・完全の比較を実行できます。

通常終了時は一時作業データを削除します。残す場合は「設定」から作業ファイルを明示的に保存します。詳細な制限とデータの扱いは [設計・安全制限](docs/python-design.md) に記載しています。

## 検証・ビルド（開発担当者）

```bat
py -3.14 -m unittest discover -s tests_python -v
py -3.14 run.py --self-test output\python-selftest.json
py -3.14 build_windows.py
```

WindowsビルドだけPyInstaller 6.22.3等を使用します。ビルド依存は `requirements-build-windows.txt` でバージョン・ハッシュを固定しています。利用者PCでビルドする必要はありません。

成果物は `artifacts/enterprise-xml-analyzer-windows-x64-python3146.zip`、ソース配布は `python scripts/package_python.py` で作成します。Windows自動ビルドは `.github/workflows/python-windows.yml` です。[検証結果](docs/python-test-report.md) を確認してください。

## 構成

`run.py` が起動入口、`xml_analyzer/` が画面・解析・SQLite・比較・抽出・逐次出力、`tests_python/` が機能とGUIのテストです。実行環境の固定は `.python-version` と `pyproject.toml` の両方に記載しています。環境変数・APIキーの設定は不要です。

旧Web版の `src/`、npm設定、IIS/Electron資材は移行履歴として保持しています。Python版の実行・配布には含めません。[旧版README](docs/legacy-web-readme.md) と旧版の性能報告を、Python版の動作保証として使わないでください。
