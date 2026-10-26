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

### 簡単出力モード（全要素）

XMLを読み込んだら「XML読込」または「Excel出力」の **全要素をExcel保存** を押し、保存先を選ぶだけで出力できます。読み込み済みの全XMLを対象に、親要素・空要素も含めて1要素1行の一覧にします。元ファイル・ファイルID・要素番号・親要素番号・階層・要素名・名前空間・要素パス・属性JSON・直接のテキスト・分割番号を出力します。

列の選択や条件設定は不要で、詳細出力の設定には影響されません。文字列の先頭ゼロを保持します。1セル32767 UTF-16単位を超える値は続きの行へ分割し、切り捨てません。1シートの上限で自動分割し、出力合計200万行の安全上限を超えた場合は保存を中止して既存ファイルを保全します。

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
