[English](https://github.com/Yamatomato0105/takasumibot-toolkit-py/blob/main/docs/README.en.md) | [日本語](https://github.com/Yamatomato0105/takasumibot-toolkit-py/blob/main/docs/README.ja.md)

# takasumibot-toolkit

[TakasumiBOT 公開 API（v3）](https://api.takasumibot.com/docs) を利用する非同期 Python クライアント SDK です。OpenAPI ドキュメントに定義された HTTP エンドポイントに対応しています。

- 配布パッケージ名: `takasumibot-toolkit` / インポート名: `takasumibot_toolkit`
- Python 3.11 以上（非同期 API のみ提供、`py.typed` 同梱）
- ランタイム依存は `httpx` と `pydantic` のみ
- 指数バックオフによるリトライ、タイムアウト、構造化エラー、任意のキャッシュを提供
- `int64` の値は Python の任意精度 `int`、`date-time` はタイムゾーン付き `datetime` として精度を保持
- 公開 API のため認証不要。WebSocket (`/v3/realtime/`) は対象外

このパッケージは現在Alpha版として開発されています。互換性のない変更が入る可能性があります。

詳細な仕様や API リファレンスは [日本語ドキュメント](https://github.com/Yamatomato0105/takasumibot-toolkit-py/blob/main/docs/README.ja.md) / [English Documentation](https://github.com/Yamatomato0105/takasumibot-toolkit-py/blob/main/docs/README.en.md) を参照してください。

## インストール

```bash
pip install --pre takasumibot-toolkit
```

## クイックスタート

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

## 主な機能

### API クライアント

税率、シャード、統計、履歴、プロフィール、ランキング、企業、株価、Discord ユーザー、ステータスなどの情報を取得できます（全 13 エンドポイント）。利用可能なメソッドとパスは、同梱の `TakasumiBOT-OpenAPI-Document.json` に基づきます。

ギフトコードはリクエスト送信前に形式（`[A-Za-z0-9]{10}`）を検証します。

```python
gift = await kit.get_gift_info("Abc123Xyz0")
```

### 株価ヘルパー

API から取得する株式一覧（`GET /v3/stock/`）をもとに、銘柄情報・現在価格・履歴をクライアント側で導出できます。同じ `StockResource` は一覧を一度だけ取得して再利用しますが、リソースごとに初回の一覧取得が必要です。クライアント全体のキャッシュは既定で無効です。履歴は**新しい順**で、価格がない場合 `price()` は `None` を返します。

```python
stock = kit.get_stock().id("JTTI")
price = await stock.price()  # int | None
info = await stock.info()  # StockEntry
history = await stock.history(limit=3)  # list[int]
```

株式一覧のキャッシュは既定で無効です。必要に応じてクライアント単位で有効化できます。

```python
async with create_kit_client(stock_cache={"ttl_ms": 60_000}) as kit:
    price = await kit.get_stock().id("JTTI").price()
```

### 設定とエラー処理

タイムアウト、共通ヘッダー、カスタム `httpx.AsyncClient`、ログ出力、リダイレクト追跡、リトライなどを `BasicConfig` またはキーワード引数で設定できます。設定値が不正な場合は `TakasumiBotToolkitConfigError` が送出されます。

```python
kit = create_kit_client(
    timeout_ms=10_000,
    retry={"max_retries": 3},
    headers={"user-agent": "my-bot/1.0.0"},
)
```

ネットワークエラー、タイムアウト、HTTP `429` / `500` / `502` / `503` / `504` はリトライ対象です。その他の HTTP エラーや入力・レスポンスの検証エラーはリトライされません。エラーはすべて `TakasumiBotToolkitError` を継承し、種類に応じて `status`、`code`、`retryable` などを参照できます。

### データモデルとヘルパー

レスポンスはイミュータブルな Pydantic モデル（`snake_case` フィールド、元の JSON キーへのエイリアス対応）として検証されます。`int64` の値（資産や価格など）は Python の任意精度 `int` として返されるため、精度落ちなく安全に扱えます。

また、文字列の切り詰め、フィールドの選択・除外、Markdown テーブル生成、数値や日時の整形（`ja-JP` / `en-US` 対応）、ページネーション、株価履歴の整形などの純粋関数を、トップレベルのエクスポートまたは `kit.helpers` から利用できます。

## 開発用コマンド

```bash
pip install -e ".[dev]"
make check    # ruff + format check + mypy + pytest + doctests + gen-check + openapi-check
make gen      # src/takasumibot_toolkit/models/generated/models.py の再生成
make live     # 本番 API に対するスモークテスト（ネットワーク接続が必要）
make sweep    # 本番 API をサンプリングしスキーマ乖離をレポート（ネットワーク接続が必要）
make build    # python -m build + twine check
```

## TakasumiBOT-OpenAPI-Document.json について

この SDK は、同梱の `TakasumiBOT-OpenAPI-Document.json` に基づき、HTTP エンドポイントを定義しています。
この OpenAPI ドキュメントは、[TakasumiBOT API](https://api.takasumibot.com/docs) から取得したものを SDK 用に情報を追加および整理したものです。SDK の更新に伴い、同梱の OpenAPI ドキュメントが更新される場合があります。
元の OpenAPI ドキュメントは TakasumiBOT API から取得できます。

## ライセンス

[MIT](https://github.com/Yamatomato0105/takasumibot-toolkit-py/blob/main/LICENSE) — Copyright (c) 2026 Yamatomato0105.
