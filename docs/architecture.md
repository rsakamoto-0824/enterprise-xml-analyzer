# 構成とデータフロー

IIS → 静的HTML/CSS/JavaScript → 利用者のブラウザ、という一方向の配信です。FileオブジェクトはWorkerへ構造化複製し、XML内容をHTTPリクエストに含めません。

1. parser.worker: 64KiBずつFile.sliceを読み、TextDecoderのstreamモードで復号。SAXイベントをノード・属性・テキストへ変換。
2. IndexedDB: 各チャンクのトランザクション完了を待って次を読む。読み込みキューは16ファイル、同時解析は1。
3. UI: parent複合索引で子を取得。ツリー・差分は可視行だけDOMへ描画。グラフは表示上限500。
4. index.worker: 要素・属性・テキスト・パスをページ単位で走査して検索。
5. diff.worker: 対応する親の子を段階照合し、差分を500行単位で保存。
6. export.worker: レコードをページ単位で抽出。XLSXは制限付き全ブック生成、CSVはチャンク要求と保存完了の応答によるバックプレッシャー。

Files / Nodes / Attributes / TextSegments / NodeIndexes / SubtreeHashes / Comparisons / DiffResults / ExportTemplatesを使用します。Nodesの主キーは[fileId,nodeId]。parent・path・nameの複合索引を持ちます。XML全文は保存せず、解析されたテキスト値だけを保存します。元Fileは現在のページのメモリ参照で保持し、IndexedDBへ複製しません。

ノードIDは文書順の整数で、構造パスとは別です。subtreeEndOrderは終了要素時の最終ノードIDです。text/cdata/comment/piも文書ノードとして保存します。siblingIndexは同じ名前空間・ローカル名の兄弟内の位置です。sourceOffsetはバイト位置を保証できないためnullです。

Web Locksで同一配信元の同時タブ利用を禁止し、起動時削除と他タブの作業の競合を防ぎます。別利用者・別PCのブラウザストレージは分離されます。UI状態とWorker状態は分離し、各Workerは同時に1処理のみ受け付けます。状態はIDLE/LOADING/PARSING/INDEXING/READY/COMPARING/EXPORTING/COMPLETED/CANCELLED/ERROR型で表現し、永続メタデータは解析中・解析済み・比較中・比較済みを区別します。
