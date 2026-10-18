[English](./README.en.md) | [日本語](./README.ja.md)

# takasumibot-toolkit

**[TakasumiBOT 公開 API](https://api.takasumibot.com/docs)**（`TakasumiBOT API 3.0.0-beta`）向けの非同期 Python クライアント SDK です。

- 配布パッケージ名: `takasumibot-toolkit`
- インポート名: `takasumibot_toolkit`
- Python: `>=3.11`（非同期 API のみ提供、`py.typed` 同梱）
- ランタイム依存は `httpx` と `pydantic` のみ
- 指数バックオフによるリトライ、タイムアウト、構造化エラー、任意のキャッシュを提供
- `format: int64` の値はすべて Python の任意精度 `int` で扱い（精度損失なし）、`date-time` はタイムゾーン付き `datetime` として検証
- **認証不要**（公開 API のため）、WebSocket クライアント（`/v3/realtime/`）は**非対応**

このリポジトリに同梱されている OpenAPI ドキュメント（`TakasumiBOT-OpenAPI-Document.json`）が唯一の信頼元です。各メソッドは宣言された HTTP パスに対応し、それ以外の呼び出しは行いません。

> WebSocket エンドポイント `/v3/realtime/` は意図的に未実装です。

## インストール

```bash
pip install --pre takasumibot-toolkit
# このリポジトリの開発用依存関係を含める場合:
pip install -e ".[dev]"
```

このパッケージはAlpha版です。互換性のない変更が入る可能性があります。

## クイックスタート（非同期）

```python
import asyncio

from takasumibot_toolkit import create_kit_client


async def main() -> None:
    async with create_kit_client() as kit:
        tax = await kit.get_tax_info()
        ranking = await kit.get_ranking()

        print(tax.income_tax_rate)  # float
        if ranking:
            profile = await kit.get_profile_by_id(ranking[0].id)
            print(profile.assets)  # int


asyncio.run(main())
```

非同期 API のみを提供しています（同期ファサードはありません）。同期コードから呼び出す場合は上記のように `asyncio.run(...)` でラップしてください。すべての機能は単一クライアントインスタンス経由で利用でき、グローバル状態はありません。

## 設定 (`BasicConfig`)

`create_kit_client()` は `BasicConfig` インスタンス、またはキーワード引数によるオーバーライドを受け付けます（両方指定した場合はキーワード引数が優先されます）。`BasicConfig` には意図的に **`base_url` フィールドがありません**。

```python
from takasumibot_toolkit import BasicConfig, create_kit_client

kit = create_kit_client(
    BasicConfig(
        timeout_ms=10_000,
        headers={"user-agent": "my-bot/1.0.0"},
        retry={
            "max_retries": 3,
            "initial_delay_ms": 300,
            "max_delay_ms": 5_000,
            "backoff_factor": 2.0,
            "jitter": True,
        },
        stock_cache={"ttl_ms": 60_000},  # 未指定時は無効
    )
)

# キーワード引数によるオーバーライド:
kit = create_kit_client(timeout_ms=3_000)
# デフォルトの 60 秒 TTL、または明示的なミリ秒指定で株式一覧キャッシュを有効化:
kit = create_kit_client(stock_cache={})
kit = create_kit_client(stock_cache={"ttl_ms": 30_000})
```

| フィールド | デフォルト | 備考 |
|---|---|---|
| `timeout_ms` | `10000` | 各試行ごとのタイムアウト（リトライごとに新しいタイムアウトが適用され、全体のデッドラインはありません）。`> 0` の整数が必須。 |
| `headers` | `None` | 追加ヘッダー。ユーザー指定のヘッダーはデフォルト（`accept`, `user-agent`）を上書きします。ヘッダー名は RFC 7230 トークン、値は印字可能 ASCII・SP・HTAB のみ許容され、違反時は送信時ではなく生成時に `TakasumiBotToolkitConfigError` を送出します。 |
| `retry` | [リトライ](#リトライ)参照 | `RetryConfig` または `dict`。未知のキーは `TakasumiBotToolkitConfigError(code="UNKNOWN_CONFIG_KEY")` を送出します。`backoff_factor` は有限かつ `>= 1.0` が必須です。 |
| `client` | `None` | 呼び出し側が所有する注入用 `httpx.AsyncClient`。SDK 側ではクローズしません（`kit.owns_http_client is False`）。`timeout_ms` と `follow_redirects` はリクエストごとに適用されるため、注入されたクライアント側の設定より本設定が優先されます。 |
| `logger` | `None` | `debug`/`info`/`warning`/`error` メソッドを持つオブジェクト（標準ライブラリの `logging.getLogger(...)` や `logging.LoggerAdapter` など）。 |
| `follow_redirects` | `False` | `False` の場合、3xx レスポンスは `TakasumiBotToolkitHttpError` を送出します。有効化するとカスタムヘッダーがリダイレクト先に転送される可能性があるため、リダイレクト追跡時には（特にホストを跨ぐ場合や HTTPS から HTTP へのリダイレクトにおいて）機密情報を含むカスタムヘッダーを使用しないでください。 |
| `stock_cache` | `None` | クライアント単位の株式一覧キャッシュ（NPM 版 `stockCache`）。`None`/`False` で無効、`{}` でデフォルト TTL（60,000 ms）で有効化、`{"ttl_ms": n}`（または `StockCacheConfig(ttl_ms=n)`）で TTL を**ミリ秒**（`> 0` の整数）で指定します。`True` やその他の型、0 以下の TTL、未知のキーは `TakasumiBotToolkitConfigError` を送出します。 |

デフォルト値は `DEFAULT_RETRY_CONFIG` および `DEFAULT_STOCK_CACHE_TTL_MS` としてエクスポートされています。

### ベース URL (`base_url`)

ベース URL のデフォルトは OpenAPI の `servers[0].url`（`https://api.takasumibot.com`）であり、環境変数 `TAKASUMIBOT_BASE_URL` でのみ変更できます（例: ローカルサーバー向けに `TAKASUMIBOT_BASE_URL=http://localhost:8000`）。
機密情報を含むヘッダーを設定する場合は HTTPS を使用してください。HTTP のベース URL はローカルテストに限定してください。ヘッダーは HTTPS へのアップグレードなしに送信されます。

解決順序:

1. `os.environ["TAKASUMIBOT_BASE_URL"]`（空文字・空白のみの場合は無視されデフォルトへフォールバック）
2. OpenAPI ドキュメントの `servers[0].url`（`https://api.takasumibot.com/`）

`http(s)` 以外の URL や、パス・クエリ・フラグメントを含む URL は `TakasumiBotToolkitConfigError` を送出します。ベース URL とパスの両方から末尾のスラッシュは取り除かれるため、リクエスト先は `https://api.takasumibot.com/v3/tax` になります。解決後の値は読み取り専用プロパティとして参照できます:

```python
kit.base_url  # 例: "https://api.takasumibot.com"
```

### タイムアウト

タイムアウトはリトライ全体ではなく**各試行ごと**に適用されます。呼び出し全体の所要時間を制限したい場合は、呼び出し側で `asyncio.timeout()` を使ってラップしてください:

```python
import asyncio

from takasumibot_toolkit import TakasumiBotToolkitTimeoutError

try:
    async with asyncio.timeout(5):
        tax = await kit.get_tax_info()
except (TimeoutError, TakasumiBotToolkitTimeoutError):
    ...  # asyncio.timeout は組み込みの TimeoutError を、各試行のタイムアウトは TakasumiBotToolkitTimeoutError を送出します
```

`asyncio.timeout()` は**組み込みの** `TimeoutError` を送出し、各試行の httpx タイムアウトは `TakasumiBotToolkitTimeoutError`（リトライ対象）になります。これらは継承関係のない別クラスであるため、両方を捕捉したい場合は明示的に両方を指定してください。

### ログ出力

任意の `LoggerLike`（標準ライブラリの `logging.Logger` や `logging.LoggerAdapter`）を渡せます。送受信イベントは `debug`、各リトライは `warning`、リトライ上限超過は `error` レベルで記録されます。メッセージは英語でリクエスト URL を含むため、プレーンなログフォーマットでも有用です:

```text
DEBUG    http request GET https://api.takasumibot.com/v3/tax
DEBUG    http response GET https://api.takasumibot.com/v3/tax status=200
WARNING  retrying request GET https://api.takasumibot.com/v3/tax
ERROR    retry limit exceeded for GET https://api.takasumibot.com/v3/tax
```

構造化コンテキスト（`method`, `url`, `status`, `attempt`, `delay_ms`, `reason`, `attempts`）は標準ロガーに `extra=` として渡されるため、`LogRecord` から参照できます。注意点:

- `logging.LoggerAdapter` の場合、アダプター自身の `extra` はツールキットのコンテキストとマージされます。アダプターが標準の `process()`（`extra` を置き換える実装）を使用している場合、ツールキットは内部のロガー経由で記録し情報が失われないようにします。カスタムの `process()` を持つ場合はその処理が維持されます。
- ログ出力はベストエフォートです。ロガーが送出した例外は握りつぶされ、リクエスト自体が失敗することはありません。

## エンドポイント

OpenAPI ドキュメントに定義されている全 13 個の HTTP パスに対応しています（それ以外のパスは呼び出しません）:

| メソッド | パス | HTTP | 戻り値 |
|---|---|---|---|
| `get_gift_info(id)` | `/v3/gift/{id}` | GET | `GiftResponse` |
| `get_tax_info()` | `/v3/tax/` | GET | `TaxResponse` |
| `get_shard_info()` | `/v3/shard/` | GET | `ShardResponse` |
| `get_statistics_info()` | `/v3/statistics/` | GET | `StatisticsResponse` |
| `get_history_by_id(id)` | `/v3/history/{id}` | GET | `list[HistoryEntry]` |
| `get_profile_by_id(id)` | `/v3/profile/{id}` | GET | `ProfileResponse` |
| `get_ranking()` | `/v3/ranking/` | GET | `list[RankingEntry]` |
| `get_company_list()` | `/v3/companylist/` | GET | `list[CompanyListEntry]` |
| `get_company_by_id(id)` | `/v3/company/{id}` | GET | `CompanyDetailResponse` |
| `get_stock_list()` | `/v3/stock/` | GET | `list[StockEntry]` |
| `get_discord_user_by_name(name)` | `/v3/discord/usersearch/{name}` | GET | `DiscordUserSearchResponse` |
| `get_company_history_by_id(id)` | `/v3/companyHistory/{id}` | GET | `list[CompanyHistoryEntry]` |
| `get_status()` | `/v3/status/` | GET | `list[StatusEntry]` |

```python
# ギフトコードはリクエスト送信前にクライアント側で /^[A-Za-z0-9]{10}$/ により検証されます。
gift = await kit.get_gift_info("Abc123Xyz0")
```

注意点:

- パス引数はまず JavaScript の `String.prototype.trim()` と同じ空白文字集合でトリムされ（そのため `" 123 "` は NPM パッケージと同様に `/v3/profile/123` へリクエストされます）、その後 `quote(value, safe="")` でパーセントエンコードされます。またパスの末尾スラッシュは除去されます。空文字や空白のみの値は `TakasumiBotToolkitValidationError(code="EMPTY_ARGUMENT")` を送出します。パス引数は型、空文字、波括弧（`{` `}`）、および単独の `.`/`..` セグメントについて検証されます（OpenAPI ドキュメントにパターン宣言がないため、ギフトコードを除きフォーマット検証は行いません）。`quote()` はドットをエスケープしないため、`.`/`..` の拒否は重要です（これがないと `get_profile_by_id("..")` が `/v3` へのリクエストに正規化され、定義外のパスへアクセスしてしまいます）。それ以外の値（`a/../../b` → `/v3/profile/a%2F..%2F..%2Fb`）はパス内に留まるため、定義された 13 パス以外にリクエストが送られることはありません。
- `get_gift_info(id)` は厳密に 10 文字の ASCII 英数字（`[A-Za-z0-9]{10}`、NPM 版の `requireGiftId` ルール）を要求します。前後に空白を含む場合を含め（NPM 版と同様にトリムされません）、一致しない値は**リクエストを送信せずに** `TakasumiBotToolkitValidationError(code="INVALID_GIFT_ID", field="id")` を送出します。
- 配列レスポンスはサーバーの順序を保持し、`list[T]` を返します。
- API は「データなし」を `404` または空/null ボディの `200` で表現する可能性があります。`404` は常に `TakasumiBotToolkitHttpError` を送出し、`200 + null` は `TakasumiBotToolkitResponseParseError` を送出します（`[]` への正規化は行いません）。
- `get_company_by_id()` は企業 ID **または**オーナーの Discord ユーザー ID を受け付けます。本番 API ではオーナー ID で検索した場合に `employees` キー自体が省略されるため、`CompanyDetailResponse.employees` は `list[CompanyEmployeeEntry] | None` となっています（`None` = キー未返却、`[]` = 空配列として返却）。

> `/v3/realtime/` は WebSocket エンドポイントであり、このパッケージの対象外です。

## Stock（クライアント側導出）

OpenAPI ドキュメントには単一銘柄のエンドポイント（`/v3/stock/{id}`）が存在しないため、個別銘柄の `info` / `price` / `history` は `GET /v3/stock/` の株式一覧からクライアント側で導出されます。各 `StockResource` は一覧を一度だけ読み込み、同時に要求されたロードも共有します。クライアント全体のキャッシュを有効にしない場合、リソースごとに初回の一覧取得が発生します:

```python
stocks = await kit.get_stock_list()

# Builder（遅延実行: 末端のメソッドを await するまでリクエストは発生しません）
stock = kit.get_stock().id("JTTI")
entry = await stock.info()          # StockEntry
price = await stock.price()         # 最新価格 = prices[-1]; prices が空なら None
history = await stock.history()     # 新しい順の list[int]
recent = await stock.history(limit=3)

# 同等のクライアントメソッド:
entry = await kit.get_stock_info_by_id("JTTI")
price = await kit.get_stock_price_by_id("JTTI")
recent = await kit.get_stock_history_by_id("JTTI", limit=3)
```

ルール:

- 銘柄 ID（stock id）は他のパス引数と同様に前後の空白がトリムされます（`" JTTI "` → `"JTTI"`）。
- `PRICES_ORDER = "last_is_newest"`: `prices` は時系列昇順（末尾の要素が最新価格）であり、`history()` は常に**新しい順**で返されます。
- `price()` は最新価格（`prices[-1]`）を返し、`prices` が空の場合は `None` を返します（エラーにはなりません）。
- `limit`: 省略時は全件、`0` は `[]`、正の整数は最新 *n* 件を返します。負数・真偽値・非整数は `TakasumiBotToolkitValidationError` を送出します。
- 一覧に存在しない銘柄 ID は `TakasumiBotToolkitValidationError(code="STOCK_NOT_FOUND")` を送出します（リトライされません。例外の代わりに `None` を得たい場合は `resolve()` を使用できます）。

### 互換性への配慮

OpenAPI ドキュメントでは `StockEntry.id` は 10 個の値の enum として宣言されていますが、レスポンスはリスト全体として検証されます。厳格な `Literal` を使うと、将来 API に 11 社目の銘柄が追加された瞬間にレスポンス全体のバリデーションが失敗し、`get_stock_list()` とそれに依存するすべての Stock ヘルパーが一斉に壊れてしまいます。

そのため OpenAPI の `enum` プロパティ（`GiftResponse.type`/`status`, `StockEntry.id`）には、NPM 版の `looseEnum` と同様に「空でない任意の文字列」を受け入れる `LooseEnum` フィールドを使用しています。ドキュメントに記載された既知の値は `GIFT_TYPES`, `GIFT_STATUSES`, `STOCK_IDS`（および `GiftType`/`GiftStatus`/`StockId` の `Literal` 型エイリアス）としてエクスポートされており、`is_known_value()` で既知の値に絞り込むことができます:

```python
from takasumibot_toolkit import STOCK_IDS, is_known_value

stocks = await kit.get_stock_list()
documented = [s for s in stocks if is_known_value(s.id, STOCK_IDS)]
```

### Stock キャッシュ

各 `StockResource` は株式一覧を高々 1 回だけ読み込みます（並列の初回呼び出しも同じロードを共有します）。クライアント全体のキャッシュは**デフォルトで無効**です（`stock_cache` を設定しない場合、リソースごとに初回のネットワークリクエストを行います）。クライアント単位で有効化できます:

```python
kit = create_kit_client(stock_cache={})                   # 有効化、TTL 60 秒 (DEFAULT_STOCK_CACHE_TTL_MS)
other = create_kit_client(stock_cache={"ttl_ms": 30_000})  # 有効化、TTL 30 秒
uncached = create_kit_client(stock_cache=False)           # 無効（デフォルト）
```

- スコープ: クライアントインスタンス単位（複数のクライアント間でキャッシュは共有されません）。
- `stock_cache` を有効にすると、NPM パッケージと同様に **`get_stock_list()` 自体も Stock ヘルパーと同じキャッシュから返されます**（single-flight: 同時に発生したキャッシュミスは 1 つのリクエストを共有します）。
- `get_stock_list(force_refresh=True)` は常に再取得を行ってキャッシュを更新します（すでに進行中の取得がある場合はその完了後に実行されるため、古い結果で上書きされることはありません）。
- 失敗したリクエストはキャッシュされません。クライアントのリトライポリシーを超えた追加リトライは行いません。
- キャッシュはエントリのディープコピーを保持し、呼び出しごとに新しいコピーを返します。そのため、受け取ったリストやエントリの `prices` を変更しても、キャッシュや後続の呼び出し結果が破損することはありません（なお `StockEntry.prices` 自体は frozen な Pydantic モデルのリストフィールドと同様にミュータブルな `list` です）。

## エラー

すべてのエラーは `TakasumiBotToolkitError` を継承しています（NPM パッケージと同じ命名の公開 8 クラス）。短縮エイリアス（`TimeoutError` や `ValidationError` など）は、組み込みの `TimeoutError`（`asyncio.timeout(...)` の `except TimeoutError:` を壊してしまう）や `pydantic.ValidationError` をシャドウイングするため提供していません。また、`TakasumiBotToolkitTimeoutError` は意図的に組み込み `TimeoutError` のサブクラスにはしていないため、両方をまとめて捕捉したい場合は `except (TimeoutError, TakasumiBotToolkitTimeoutError):` のように明示的に指定してください。

| クラス | 発生条件 | `retryable` |
|---|---|---|
| `TakasumiBotToolkitError` | すべてのツールキット例外の基底クラス | `False`（デフォルト） |
| `TakasumiBotToolkitConfigError` | 不正な設定、不正なベース URL、クローズ済みクライアントの使用 | `False` |
| `TakasumiBotToolkitValidationError` | 不正な引数（パス引数、stock の `limit`、未知の銘柄 ID、不正なギフト ID、ヘルパー引数） | `False` |
| `TakasumiBotToolkitHttpError` | 2xx 以外のステータス（404 や未追跡の 3xx を含む） | ステータスが `{429, 500, 502, 503, 504}` のとき `True` |
| `TakasumiBotToolkitNetworkError` | 接続・トランスポート層の失敗 | 通常 `True` |
| `TakasumiBotToolkitTimeoutError` | 各試行のタイムアウト超過 | `True` |
| `TakasumiBotToolkitRetryLimitError` | リトライ上限の枯渇（`attempts`, `delays_ms`, `last_error` を保持） | `False` |
| `TakasumiBotToolkitResponseParseError` | 2xx レスポンスの空ボディ・不正な JSON・スキーマ不一致（`issues`, `raw_body` を保持） | `False` |

```python
from takasumibot_toolkit import (
    TakasumiBotToolkitError,
    TakasumiBotToolkitHttpError,
    TakasumiBotToolkitRetryLimitError,
    TakasumiBotToolkitValidationError,
)

try:
    await kit.get_gift_info("nope")
except TakasumiBotToolkitValidationError as exc:
    print(exc.field, exc.code)  # "id" "INVALID_GIFT_ID"
except TakasumiBotToolkitHttpError as exc:
    print(exc.status, exc.request_id)
except TakasumiBotToolkitRetryLimitError as exc:
    print(exc.attempts, exc.last_error)  # exc.last_error に最後の Http/Network/Timeout エラーが入ります
except TakasumiBotToolkitError as exc:
    if exc.retryable:
        ...  # 外側での再試行が有効な場合があります
```

`TakasumiBotToolkitHttpError` は `status`（`status_code` エイリアスあり）、`method`、`url`、およびレスポンスからベストエフォートで抽出された `api_code`/`api_message`/`request_id` を保持します（OpenAPI ドキュメントにエラーレスポンスのスキーマ定義がないため、これらは `None` になる場合があります）。NPM 版と同様に、レスポンス本文全体を `raw_body`（空ボディなら `""`）、生の `Retry-After` ヘッダー値を `retry_after`、そして許可リストに含まれるレスポンスヘッダー（`content-type`, `retry-after`, `x-request-id`, `x-requestid`, `request-id`, `x-takasumibot-request-id`, `x-ratelimit-remaining`）を `headers` として保持します。JSON ではない本文を持つ非 2xx レスポンスも `TakasumiBotToolkitHttpError` となり、**2xx** レスポンスのみが `TakasumiBotToolkitResponseParseError`（同様に `raw_body` を保持）を送出します。

バリデーションエラーの `code` は NPM パッケージに準拠しています: `EMPTY_ARGUMENT`（空白のみの引数）、`INVALID_GIFT_ID`、`INVALID_STOCK_ID`、`STOCK_NOT_FOUND`、`UNSAFE_PATH_PARAMETER`、`INVALID_TYPE`、`INVALID_LIMIT`、`INVALID_ARGUMENT`、`INVALID_PAGE`、`INVALID_PAGE_SIZE`。

すべてのエラークラスは `pickle`、`copy.copy`、`copy.deepcopy` に対応しているため、`multiprocessing`、Celery、pytest-xdist、`ProcessPoolExecutor` などのプロセス境界を越えて受け渡すことができます（標準ライブラリの例外と同様に `__cause__`/`__context__` は引き継がれませんが、`RetryLimitError.last_error` は保持され復元時に `__cause__` として再設定されます）。

## リトライ

デフォルト設定（NPM 版の `DEFAULT_RETRY_CONFIG` と同一で、`DEFAULT_RETRY_CONFIG` としてエクスポート）:
`max_retries=3`（リトライ回数であり、初回を含め最大 4 回試行）、`initial_delay_ms=300`、`max_delay_ms=5000`、`backoff_factor=2.0`、`jitter=True`。

```python
kit = create_kit_client(retry={"max_retries": 0})  # リトライを無効化
```

- リトライ対象: ネットワークエラー、タイムアウト、および HTTP `429/500/502/503/504`。これには一時的な Keep-Alive 切断である `httpx.RemoteProtocolError`（"Server disconnected without sending a response"）も含まれます（すべてのリクエストがべき等な GET であるため安全にリトライ可能です）。
- リトライ非対象: その他の 4xx、バリデーションエラー、設定エラー、レスポンスパースエラー、`httpx.LocalProtocolError`、リダイレクト回数超過、プロキシエラー。キャンセル（`asyncio.CancelledError`）は決して捕捉・リトライされません。
- 待機時間: `min(initial * factor**attempt, max_delay)` に 50〜100% のジッターを掛け、`ceil` で切り上げます（基本待機時間が `> 0` の場合は最小 1 ms）。大きな `attempt` 値でもオーバーフローせずクリップされます。
- `Retry-After` ヘッダー（秒数または HTTP-date）がある場合はバックオフより優先され、`max_delay_ms * 3` にクリップされます。デフォルト設定では最大 **15 秒**となるため、サーバーが `Retry-After: 60` を返した場合でも 15 秒後にリトライされます。サーバー指定の長い待機時間をそのまま尊重したい場合は `max_delay_ms` を大きくしてください。
- `max_retries=0` の場合は元の例外がそのまま送出されます。それ以外でリトライ上限に達した場合は、最後のエラーをチェーンした `TakasumiBotToolkitRetryLimitError`（`attempts`, `delays_ms`, `last_error` を保持）が送出されます。

## ヘルパー

副作用のないオフラインの純粋関数群です。トップレベルからエクスポートされているほか、NPM 版の `kit.helpers` と同様に各クライアントの読み取り専用名前空間（`kit.helpers.format_number` は `format_number` と同一）としてもアクセスできます。出力は NPM パッケージと一致します（`Intl.NumberFormat`/`Intl.RelativeTimeFormat` の挙動をデフォルトロケール `DEFAULT_LOCALE = "ja-JP"` および `"en-US"` で再現しています）:

| ヘルパー | 説明 |
|---|---|
| `truncate(text, max_length, *, ellipsis="...")` | コードポイント単位で文字列を切り詰めます（省略記号の長さも `max_length` に含まれます）。 |
| `pick_fields(obj, keys)` / `omit_fields(obj, keys)` | **単一の**モデル・Mapping・dataclass・namedtuple から正規（`snake_case`）フィールドを選択または除外して `dict` を返します。NPM パッケージと同様にリストは拒否され（要素ごとに適用してください）、`pick_fields` は `keys` で指定した順序でキーを返します。`keys` は `str` のシーケンスである必要があり、単一の `"id"` を渡すと `"i"`, `"d"` と解釈される代わりに `TakasumiBotToolkitValidationError` を送出します。 |
| `to_markdown_table(rows, *, headers=None, align=None)` | Markdown テーブル（`\| a \| b \|` / `\| --- \| --- \|`）を生成します。各行はレコード（モデル、Mapping、dataclass など）である必要があります。列はデフォルトで**最初の行**のキー順となり、`align`（列ごとの `"left"`/`"center"`/`"right"`）で `:---`/`:---:`/`---:` を出力します。セルとヘッダーの両方で `\|` はエスケープされ改行は `<br>` に変換されます。`None` は空セル、float は `repr`、datetime は `format_timestamp` の ISO 形式となり、空の `rows` や列数 0 の場合は `""` を返します。 |
| `format_number(value, *, locale=None, minimum_fraction_digits=None, maximum_fraction_digits=None, notation=None, compact_display=None)` | `Intl.NumberFormat` 互換の数値整形: `1234.5678` → `1,234.568`、`notation="compact"` → `1.2万`/`1.5億`（`ja-JP`）または `1.2K`（`en-US`）。Python の任意精度 `int` を精度落ちなく扱えます。`NaN`/`Inf`/`None`/`bool` は拒否され、`-0` が出力されることはありません。 |
| `format_timestamp(value, *, format="iso", locale=None, time_zone=None, reference=None)` | `value` にはタイムゾーン付き `datetime`、ISO 文字列、または **エポックミリ秒**（`int`/`float`）を渡せます。`iso` → `2024-01-01T00:00:00.000Z`、`locale`（`time_zone="Asia/Tokyo"` 指定時）→ `2024/1/1 9:00:00`、`relative` → `5 分前` / `5 minutes ago`。`reference`（Python 独自オプション）で `relative` の基準時刻を固定できます。 |
| `paginate(items, page=1, page_size=25)` | 1 始まりのページネーションを行い、`Page[T]`（`items`, `page`, `page_size`, `total_items`, `total_pages`, `has_next`, `has_prev`）を返します。範囲外のページはエラーではなく空リストとなり、`items` には本物のシーケンスが必要です（Mapping や `str`/`bytes` は拒否されます）。 |
| `get_recent_stock_history(entry_or_prices, limit=None)` | Stock の `limit` ルールに従い、新しい順の株価履歴リストを返します。 |

すべてのヘルパーは純粋関数かつオフラインで動作し、不正な引数に対しては `TakasumiBotToolkitValidationError(code="INVALID_ARGUMENT", field=...)`（`paginate` のページ範囲エラーでは `INVALID_PAGE` / `INVALID_PAGE_SIZE`）を送出します。

```python
from takasumibot_toolkit import (
    format_number,
    format_timestamp,
    get_recent_stock_history,
    omit_fields,
    paginate,
    pick_fields,
    to_markdown_table,
    truncate,
)

truncate("hello world", 8)  # "hello..."（省略記号も max_length に含まれる）
pick_fields({"a": 1, "b": 2, "c": 3}, ["a", "c"])  # {"a": 1, "c": 3}
omit_fields({"a": 1, "b": 2, "c": 3}, ["b"])  # {"a": 1, "c": 3}

to_markdown_table([
    {"name": "JTTI", "price": 103},
    {"name": "KENTAI", "price": None},
])
# | name | price |
# | --- | --- |
# | JTTI | 103 |
# | KENTAI |  |

to_markdown_table([{}])  # "" — 列がない場合は空文字を返す

format_number(1234567)  # "1,234,567"（デフォルトは ja-JP）
format_timestamp("2024-01-01T00:00:00Z")  # "2024-01-01T00:00:00.000Z"

page = paginate([1, 2, 3, 4, 5], 1, 2)
# Page(items=[1, 2], page=1, page_size=2, total_items=5, total_pages=3, has_next=True, has_prev=False)
paginate([1, 2, 3], 10, 2).items  # [] — 範囲外のページはエラーではなく空リスト

get_recent_stock_history([100, 101, 102, 103], 2)  # [103, 102]
```

## 独自コードでのテスト

`httpx.MockTransport` を組み込んだ `httpx.AsyncClient` を注入することで、実際のネットワーク接続や追加のモックライブラリなしでテストを行えます:

```python
import asyncio
import httpx

from takasumibot_toolkit import create_kit_client


def handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=[])

async def main():
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        kit = create_kit_client(client=client, retry={"max_retries": 0})
        ranking = await kit.get_ranking()

asyncio.run(main())
```

## データモデルに関する注意点

- モデルはフィールド名に `snake_case` を使用し、OpenAPI 上の名前をエイリアスとして保持します（`populate_by_name=True`）。`model_dump(by_alias=True)` で元の JSON キー名を復元できます。ドキュメントに記載されたすべてのプロパティは必須であり、キーの欠落や型の不一致は `TakasumiBotToolkitResponseParseError` を送出します。
- 本番 API の挙動が OpenAPI ドキュメントと異なるため、2 つのプロパティのみ例外としています（どちらも生成モジュールを手動編集するのではなく、`scripts/generate_models.py` のオーバーライドとして宣言されています）:
  - `DiscordUserSearchResponse.accent_color` は `int | None`（アクセントカラー未設定時に Discord が `null` を返すため）
  - `CompanyDetailResponse.employees` は `list[CompanyEmployeeEntry] | None = None`（オーナーの Discord ID で企業を検索した際にキー自体が返されないため）
- 未知のフィールドは破棄されず保持されます（`extra="allow"`、`model_extra` から参照可能）。
- OpenAPI の `enum` プロパティ（`GiftResponse.type`/`status`, `StockEntry.id`）は NPM 版の `looseEnum` と同様に `LooseEnum` フィールドとなっており、空でない任意の文字列を受け入れます。そのため、将来 API に新しい値が追加されてもレスポンス全体が壊れることはありません。既知の値は `GIFT_TYPES`, `GIFT_STATUSES`, `STOCK_IDS`（および `GiftType`/`GiftStatus`/`StockId` の `Literal` エイリアス）として公開されており、`is_known_value(entry.id, STOCK_IDS)` で既知の値かどうかを判定できます。
- `int64`/`int32` は Python の `int`（任意精度、精度損失なし・範囲チェックなし）、`number` は `float`（有限値のみ）、`date-time` はタイムゾーン付き `datetime`（naive な日時は拒否）にマッピングされます。
- `ShardDataEntry` のカウント類は OpenAPI ドキュメントで `string` と宣言されているため `str` となります。数値として扱う場合は `int(...)` で変換してください。

## 既知の制限事項・注意点

- `prices` には**タイムスタンプが含まれない**ため、`history()` から各価格がいつ記録されたかを知ることはできません。「末尾が最新」という順序は確認済みの規約（`PRICES_ORDER`）であり、OpenAPI ドキュメント上の宣言ではありません。
- エラーレスポンスのボディ構造は OpenAPI ドキュメントで定義されていないため、エラー詳細の抽出はベストエフォートであり `None` になる場合があります。
- 単位の変換は行いません（税率、`ping`、CPU/メモリ使用率、配当単位などは API が返す値のまま返却されます）。
- WebSocket クライアント（`/v3/realtime/`）、認証、同期 API、クライアント側のレート制限はありません。
- モデルはイミュータブル（`frozen=True`）ですが、`list` フィールドを持つモデル（`StockEntry.prices` や `CompanyDetailResponse.employees` など）は**ハッシュ可能（hashable）ではない**ため、`dict` のキーや `set` の要素としては使用できません。
- クライアントは生成時のイベントループに紐付きます。1 つのクライアントを複数の `asyncio.run()` 呼び出し間で再利用すると httpx から `RuntimeError: Event loop is closed` が送出されるため、イベントループごとにクライアントを作成してください（`async with create_kit_client() as kit: ...`）。
- `format_number` および `format_timestamp(format="locale"/"relative")` は `ja`/`ja-JP` と `en`/`en-US` の Intl 出力のみを再現します。その他のロケールは推測せず `TakasumiBotToolkitValidationError(field="locale")` を送出します。
- `format_timestamp(..., time_zone="Asia/Tokyo")` には IANA タイムゾーンデータベースが必要です。Linux や macOS には標準搭載されていますが、Windows では `tzdata` パッケージを別途インストールしてください（ランタイム依存を `httpx` と `pydantic` のみに保つため）。
- `time_zone` を省略した `format_timestamp(format="locale")` はホストのローカルタイムゾーンを使用します。Windows の C ランタイムは 1970 年以前や 3000 年以降の日時をローカル時刻へ変換できないため、それらの値は `TakasumiBotToolkitValidationError(field="value")` となります（フォーマットする場合は `time_zone=` を明示的に指定してください）。

## 開発

```bash
pip install -e ".[dev]"
make check    # ruff + format check + mypy + pytest + doctests + gen-check + openapi-check
make gen      # src/takasumibot_toolkit/models/generated/models.py の再生成
make live     # 本番 API に対するスモークテスト（ネットワーク接続が必要）
make sweep    # 本番 API をサンプリングしスキーマ乖離をレポート（ネットワーク接続が必要）
make build    # python -m build + twine check
```

生成されたモデルはリポジトリにコミットされており、CI（`.github/workflows/ci.yml`）が `scripts/generate_models.py --check` で最新性を検証し、`scripts/check_openapi_consistency.py` が OpenAPI ドキュメント以外の HTTP パスが使用されていないことを検証します。CI ではプッシュおよびプルリクエストごとに、リント、型チェック、テストマトリクス（Ubuntu/macOS/Windows 上の Python 3.11〜3.14、カバレッジ 90% ゲート）、doctest、および配布パッケージのビルド検証を実行します。

> **Note:** `.github/workflows/ci.yml` をプッシュするには `workflows` 権限を持つトークンが必要です。権限のない GitHub App インストレーショントークンでは *"refusing to allow a GitHub App to create or update workflow"* として拒否されるため、自身の認証情報でコミット・プッシュするか、App に `Workflows: read and write` 権限を付与してください。

`tests/test_live.py` は実際の API にアクセスするため、デフォルトでは `live` pytest マーカーにより除外されています。実行する場合は `make live`（または `pytest -m live`）を使用してください（API に到達できない場合は自動的にスキップされます）。

`scripts/live_sweep.py`（`make sweep`）は本番 API をサンプリングしてスキーマの乖離（パース失敗、未知のレスポンスフィールド、各フィールドが `null` や欠落になる頻度）をレポートします。`accentColor: null`（H-1）や `employees` の欠落（H-2）はこのツールで検出されました。検出された差異は生成ファイルを手動編集するのではなく、`scripts/generate_models.py` の `NULLABLE_OVERRIDES` / `OPTIONAL_OVERRIDES` テーブルに追加します。終了コードは `0`（乖離なし）、`1`（乖離あり）、`2`（API 到達不能）です。ステージング環境に向ける場合は `TAKASUMIBOT_BASE_URL` を、実コードで `/v3/gift/{id}` を検証する場合は `TAKASUMIBOT_LIVE_GIFT_CODE`（カンマ区切り）を設定してください。

## TakasumiBOT-OpenAPI-Document.json について

この SDK は、同梱の `TakasumiBOT-OpenAPI-Document.json` に基づき、HTTP エンドポイントを定義しています。
この OpenAPI ドキュメントは、[TakasumiBOT API](https://api.takasumibot.com/docs) から取得したものを SDK 用に情報を追加および整理したものです。SDK の更新に伴い、同梱の OpenAPI ドキュメントが更新される場合があります。
元の OpenAPI ドキュメントは TakasumiBOT API から取得できます。

## ライセンス

[MIT](../LICENSE) — Copyright (c) 2026 Yamatomato0105.
