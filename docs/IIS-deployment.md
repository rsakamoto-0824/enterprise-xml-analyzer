# IIS導入・更新・復旧

対象: Windows Server 2019/2022/2025、IIS 10、Windows 10/11上のChromium版Edge。検証端末と未検証環境はテスト報告を参照してください。会社でCodexやClaude Codeを起動する必要はありません。

## 初回導入

1. 管理者がIISの静的コンテンツ、既定のドキュメント、要求フィルター機能を有効にします。Windows認証を使う場合は該当役割サービスも追加します。
2. 社内CAなどの正当な証明書でHTTPSバインドを設定します。信頼されない証明書の警告を無視する運用にしません。
3. アプリ専用の空フォルダ `C:\inetpub\xmlviewer` を作成します。他アプリと物理ディレクトリを共有しません。
4. IISサイト直下または既存サイトの仮想ディレクトリ `/xmlviewer` に、このフォルダを割り当てます。専用サイト／アプリケーションプールならNo Managed Codeを選択します。単なる仮想ディレクトリでも静的配信できます。
5. IISの認証・承認規則で社内の必要な利用者だけを許可し、匿名公開の可否を管理者が決定します。公開フォルダはIIS実行主体に読み取り、配備担当者に書き込みを付与します。
6. ZIPを管理用の `C:\Deploy\XmlAnalyzer` へ展開します。公開するのはwwwrootの内容だけです。

```powershell
cd C:\Deploy\XmlAnalyzer\deployment
.\deploy.ps1 -Source ..\wwwroot -Destination C:\inetpub\xmlviewer -BackupRoot C:\Deploy\XmlAnalyzerBackups -DryRun
.\deploy.ps1 -Source ..\wwwroot -Destination C:\inetpub\xmlviewer -BackupRoot C:\Deploy\XmlAnalyzerBackups
.\verify-deployment.ps1 -Url https://internal-server/xmlviewer/
```

IIS役割の追加・バインド・認証設定には管理者権限が必要です。配備スクリプト自体は対象フォルダの書き込み権限があれば動作する設計です。PowerShell実行ポリシーは社内ルールに従い、無条件の永続Bypassを設定しないでください。

## 更新

利用者に作業データの出力とページ終了を依頼し、必要なら管理者が対象サイト／アプリだけを保守状態にします。静的IISではapp_offline.htmだけを置いてもアクセス停止を保証しないため、これに依存しません。IIS全体の再起動は不要です。

deployは配布元のSHA-256を検証し、既存のアプリ管理対象ファイルを公開フォルダ外へバックアップしてからコピーします。対象が空でない場合、正しいアプリ識別manifestがなければ拒否します。旧ハッシュ付き資材は削除しません。別アプリや不明なファイルも削除しません。更新中の完全な原子的切替は保証しないため、利用停止の時間帯に実施してください。配布処理中の失敗はログで確認し、下記の復旧を実行します。

## 復旧

バックアップの時刻付きフォルダを読み取り確認し、復旧対象を明示します。

```powershell
.\rollback.ps1 -Backup C:\Deploy\XmlAnalyzerBackups\20261018-150000-000 -Destination C:\inetpub\xmlviewer -SafetyBackupRoot C:\Deploy\RollbackSafety -DryRun
.\rollback.ps1 -Backup C:\Deploy\XmlAnalyzerBackups\20261018-150000-000 -Destination C:\inetpub\xmlviewer -SafetyBackupRoot C:\Deploy\RollbackSafety
.\verify-deployment.ps1 -Url https://internal-server/xmlviewer/
```

## 受入確認

- URLの末尾に `/` を付け、index/JS/CSS/4種類のWorkerの200応答と正しいMIMEを確認。
- IISの500.19は構成セクションのロック、重複、役割不足を調べる。既存設定を一括解除しない。
- Edgeで1MBと100MBの合成XMLを読み込み、ツリー・検索・比較・XLSX/CSV保存を確認。
- 開発者ツールNetworkでXML内容を含む送信、外部ホスト、CDNがないことを確認。配信は社内ネットワークで完結。
- 認証対象外のユーザーがアクセスできないことと、正規利用者のアクセスを確認。
- Windows版Excelで全シートを開き、修復警告なし、文字列ID、先頭ゼロ、日付、行数、列順を確認。
- Windowsの表示倍率125%/150%、日本語・長いファイル名、UNC共有上の手動ファイル選択を確認。UNCはEdgeのポリシーと利用者権限に依存し、Webアプリはパスへ直接アクセスしません。

`verify-deployment.ps1`は証明書検証を無効化せず、DefaultCredentialsでWindows認証に対応します。実際のXML処理やダウンロード操作まで検証するものではありません。
