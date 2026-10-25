# Python版の検証記録

基準日: 2026年10月25日。Python 3.14.6の実行結果を記録します。旧Web版の性能値・テスト件数はPython版へ流用しません。

## 確認方法

```sh
python3 -m unittest discover -s tests_python -v
python3 run.py --self-test output/python-selftest.json
python3 scripts/performance_python.py --cases 1,10,100,500,million
```

Windowsでは `python3` を `py -3.14` に置き換えます。Windowsビルドはテスト後に同梱EXEの自己検証も行います。通常のPythonをPATHから外し、PYTHONHOME/PYTHONPATHを存在しないパスに設定した状態で、日本語・空白を含むフォルダからEXEを起動します。

実測結果とWindowsビルド結果は検証完了後に本書へ追記します。会社固有の実行制限、Windows 11実機、Windows Server 2019、UNC権限、Excelの警告・再保存は会社環境の受入確認として扱います。
