# セキュリティ設計

- XML処理はFile API、Worker、IndexedDBのみ。HTTPアップロード・解析API・外部API・CDN・テレメトリはありません。
- 本番CSPはconnect-src 'none'、script-src 'self'、worker-src 'self'、object-src 'none'、frame-ancestors 'none'。D3と仮想行の位置・幅を動的設定するためstyle-srcのみunsafe-inlineを許可します。スクリプトのunsafe-inline/unsafe-evalは許可しません。
- XML値のUI表示はtextContent。innerHTMLへXMLを入れません。HTML風のテキストはそのまま表示します。
- DTD禁止、外部エンティティ無効、深さ・トークン・候補数・出力容量を制限。
- 同一配信元の同時タブ利用はWeb Locksで排他。異常終了時の残留を起動時に削除。
- 永続保存は明示設定。既定はセッション用。ログにXML値・ファイル名を入れず、自動送信しません。
- 標準エラーは分類と位置のみで、パーサー内部の入力断片を露出しません。
- XMLエクスポートのXLSXは文字列型で、CSVは数式先頭をエスケープ。

IndexedDBはアプリ固有の暗号化を行いません。OSのアカウント、端末暗号化、ブラウザプロファイルを組織で管理してください。ブラウザはストレージを自動消去する場合があり、永続保存をデータ保管の正本にしません。終了時削除はベストエフォートで、クラッシュ時には次回起動までデータが残る場合があります。

IIS認証・HTTPS・証明書・権限は企業側で設定します。独自のパスワード管理は実装しません。Windows認証は役割サービスとAD要件に合わせて管理者が設定してください。静的配信ではアプリ自身はログイン状態を管理しません。

開発用ViteはHMR通信を行うため本番CSPとは異なります。無通信の受入検証は `dist/` をIISまたは `scripts/serve.mjs` で配信して行ってください。
