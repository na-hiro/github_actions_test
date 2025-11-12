#!/usr/bin/env python3
import os
import csv
import datetime
import requests
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# ==============================
# 環境変数・設定読み込み
# ==============================

# ローカル実行時のみ .env を読む（GitHub Actions 上では不要）
env_path = Path(__file__).with_name(".env")
if env_path.exists():
    load_dotenv(env_path)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SLACK_USER_TOKEN = os.getenv("SLACK_USER_TOKEN")
PAGES_URL = os.getenv("PAGES_URL", "https://na-hiro.github.io/github_actions_test/list.html")

assert OPENAI_API_KEY, "OPENAI_API_KEY が設定されていません"
assert SLACK_USER_TOKEN, "SLACK_USER_TOKEN が設定されていません"

# Slack 投稿先（必要に応じて変更）
SLACK_CHANNEL = "all-動作検証用"

# OpenAI / Slack クライアント
openai_client = OpenAI(api_key=OPENAI_API_KEY)
slack_client = WebClient(token=SLACK_USER_TOKEN)


# ==============================
# ティッカー設定読み込み
# ==============================

def load_symbols():
    """
    tickers.csv からシンボル設定を読み込む。
    カラム: type,symbol,label

    type:
      - index: 主要指数（主要指数セクション + 総合チャート対象）
      - stock: 個別銘柄（ランキング対象）
      - gold : 金などコモディティ（任意、別枠表示）
    """
    cfg = Path(__file__).with_name("tickers.csv")
    index_symbols = {}
    stock_symbols = {}
    gold_symbols = {}

    if not cfg.exists():
        # フォールバック（設定ファイルがない場合）
        index_symbols = {
            "^NKX": "日経平均",
            "^TPX": "TOPIX",
        }
        stock_symbols = {
            "7203.JP": "トヨタ自動車",
            "6758.JP": "ソニーグループ",
            "9984.JP": "ソフトバンクG",
            "7974.JP": "任天堂",
            "8035.JP": "東京エレクトロン",
            "8306.JP": "三菱UFJFG",
            "8316.JP": "三井住友FG",
            "6861.JP": "キーエンス",
        }
        return index_symbols, stock_symbols, gold_symbols

    with cfg.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = (row.get("type") or "").strip().lower()
            sym = (row.get("symbol") or "").strip()
            label = (row.get("label") or "").strip()
            if not t or not sym or not label:
                continue

            if t == "index":
                index_symbols[sym] = label
            elif t == "stock":
                stock_symbols[sym] = label
            elif t == "gold":
                gold_symbols[sym] = label

    # 安全策：index が空ならデフォルト追加
    if not index_symbols:
        index_symbols["^NKX"] = "日経平均"
        index_symbols["^TPX"] = "TOPIX"

    return index_symbols, stock_symbols, gold_symbols


INDEX_SYMBOLS, STOCK_SYMBOLS, GOLD_SYMBOLS = load_symbols()


# ==============================
# データ取得（Stooq）
# ==============================

def fetch_from_stooq(symbol: str):
    """
    Stooq CSV (日足) から直近2営業日の終値を取得し、
    現在値・前日終値・前日比・前日比率を返す。
    URL: https://stooq.com/q/d/l/?s=<symbol>&i=d
    """
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[ERROR] {symbol}: HTTPエラー: {e}")
        return None

    lines = resp.text.strip().splitlines()
    if len(lines) <= 1:
        print(f"[WARN] {symbol}: 行数不足: {len(lines)}")
        return None

    reader = csv.DictReader(lines)
    rows = [row for row in reader if row.get("Close") not in ("", "0", "0.00", None)]

    if len(rows) < 2:
        print(f"[WARN] {symbol}: 有効なデータ行が足りません")
        return None

    # 日付でソートして直近2営業日を使用
    rows.sort(key=lambda r: r["Date"])
    latest = rows[-1]
    prev = rows[-2]

    try:
        price = float(latest["Close"])
        prev_close = float(prev["Close"])
    except ValueError:
        print(f"[WARN] {symbol}: 数値変換エラー latest={latest['Close']}, prev={prev['Close']}")
        return None

    if prev_close == 0:
        print(f"[WARN] {symbol}: 前日終値が0")
        return None

    change = price - prev_close
    change_pct = change / prev_close * 100.0

    return {
        "price": price,
        "prev_close": prev_close,
        "change": change,
        "change_pct": change_pct,
    }


# ==============================
# 各セクション生成
# ==============================

def build_index_section() -> str:
    """主要指数セクション（INDEX_SYMBOLS に基づく）"""
    lines = []
    for symbol, label in INDEX_SYMBOLS.items():
        q = fetch_from_stooq(symbol)
        if q:
            sign = "+" if q["change"] >= 0 else ""
            lines.append(
                f"- {label}: {q['price']:.2f} "
                f"({sign}{q['change']:.2f}, {sign}{q['change_pct']:.2f}%)"
            )
        else:
            lines.append(f"- {label}: データ取得失敗")
    return "\n".join(lines)


def build_stock_rankings() -> str:
    """指定銘柄(type=stock)の騰落率ランキング"""
    results = []
    for symbol, name in STOCK_SYMBOLS.items():
        q = fetch_from_stooq(symbol)
        if not q:
            continue
        results.append(
            {
                "symbol": symbol,
                "name": name,
                "price": q["price"],
                "change_pct": q["change_pct"],
            }
        )

    if not results:
        return "■ 指定銘柄: データ取得に失敗しました。"

    # 騰落率でソート
    sorted_by = sorted(results, key=lambda x: x["change_pct"], reverse=True)
    gainers = sorted_by[:3]
    losers = sorted(sorted_by[-3:], key=lambda x: x["change_pct"])

    def fmt(e):
        sign = "+" if e["change_pct"] >= 0 else ""
        return f"{e['name']} ({sign}{e['change_pct']:.2f}%)"

    lines = []
    lines.append("■ 値上がり率 上位（指定銘柄）")
    for e in gainers:
        lines.append(f"- {fmt(e)}")

    lines.append("■ 値下がり率 上位（指定銘柄）")
    for e in losers:
        lines.append(f"- {fmt(e)}")

    up = sum(1 for r in results if r["change_pct"] > 0)
    down = sum(1 for r in results if r["change_pct"] < 0)
    flat = len(results) - up - down
    lines.append(f"■ 地合い（指定銘柄ベース）: 上昇 {up}, 下落 {down}, 横ばい {flat}")

    return "\n".join(lines)


def build_gold_section() -> str:
    """金価格など(type=gold)のセクション。存在しない場合は空文字。"""
    if not GOLD_SYMBOLS:
        return ""

    lines = ["■ 金価格など"]
    for symbol, label in GOLD_SYMBOLS.items():
        q = fetch_from_stooq(symbol)
        if q:
            sign = "+" if q["change"] >= 0 else ""
            lines.append(
                f"- {label}: {q['price']:.2f} "
                f"({sign}{q['change']:.2f}, {sign}{q['change_pct']:.2f}%)"
            )
        else:
            lines.append(f"- {label}: データ取得失敗")
    return "\n".join(lines)


def build_market_snapshot_text() -> str:
    """全体スナップショット（指数＋指定銘柄＋金）"""
    jst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    header = f"日本株マーケットスナップショット (JST {jst:%Y-%m-%d %H:%M:%S})"

    index_section = build_index_section()
    ranking_section = build_stock_rankings()
    gold_section = build_gold_section()

    parts = [
        header,
        "■ 主要指数",
        index_section,
        ranking_section,
    ]
    if gold_section:
        parts.append(gold_section)

    return "\n\n".join(parts)


# ==============================
# GPT によるサマリー生成
# ==============================

def build_summary(market_snapshot: str) -> str:
    """
    Stooqベースのスナップショットをもとに、
    日本語マーケットサマリー文を生成。
    """
    prompt = f"""
以下は、日本株市場（主要指数＋指定銘柄＋金価格など）のスナップショットです：

{market_snapshot}

この情報をもとに、日本株マーケットサマリーを日本語で作成してください。

要件:
- 先頭に「【日本株マーケットサマリー】」と入れる
- 箇条書き 3〜7行程度
- 指数（日経平均・TOPIXなど）の方向感
- 指定銘柄の値上がり/値下がりから読み取れるテーマ
- 金価格などが含まれていれば、その動きも簡潔に触れる
- 地合い（全面高・全面安・まちまち 等）を一言で示す
- データ取得失敗があれば、その旨も正直に触れる
- 初心者にも分かりやすい日本語でまとめる
- 最後に必ず次の一文を含める：
  「※このサマリーはStooq等のデータを元に自動生成された参考情報であり、正確性・完全性・将来の成果を保証するものではありません。」

出力はSlackに投稿可能なテキストのみ。
"""

    res = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "あなたは日本株および関連指標の動向を簡潔かつ中立的に要約するアナリストです。",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=0.4,
    )

    return res.choices[0].message.content.strip()


# ==============================
# Slack 投稿
# ==============================

def post_to_slack(text: str):
    try:
        resp = slack_client.chat_postMessage(
            channel=SLACK_CHANNEL,
            text=text,
        )
        print("Slack への投稿に成功しました。ts:", resp["ts"])
    except SlackApiError as e:
        print("Slack 投稿エラー:", e.response.get("error"))
        raise


# ==============================
# メイン処理
# ==============================

def main():
    snapshot = build_market_snapshot_text()
    print("=== Market snapshot (raw) ===")
    print(snapshot)

    summary = build_summary(snapshot)
    print("=== Generated summary ===")
    print(summary)

    # GitHub Pages のインタラクティブチャートURLも案内として付加
    message = (
        f"【インタラクティブチャート（GitHub Pages）】\n{PAGES_URL}\n\n"
        f"{snapshot}\n\n"
        f"{summary}"
    )

    post_to_slack(message)


if __name__ == "__main__":
    main()
