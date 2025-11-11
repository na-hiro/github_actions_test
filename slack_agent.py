#!/usr/bin/env python3
import os
import datetime
import requests
from openai import OpenAI
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# ==== 環境変数 ====
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SLACK_USER_TOKEN = os.getenv("SLACK_USER_TOKEN")
ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY")

assert OPENAI_API_KEY, "OPENAI_API_KEY が設定されていません"
assert SLACK_USER_TOKEN, "SLACK_USER_TOKEN が設定されていません"
assert ALPHAVANTAGE_API_KEY, "ALPHAVANTAGE_API_KEY が設定されていません"

# 投稿先チャンネル（必要に応じて変更）
SLACK_CHANNEL = "all-動作検証用"  # うまくいかない場合は "#all-動作検証用" に

openai_client = OpenAI(api_key=OPENAI_API_KEY)
slack_client = WebClient(token=SLACK_USER_TOKEN)


# =========================================================
# Alpha Vantage ラッパ
# =========================================================

def fetch_global_quote(symbol: str):
    """
    Alpha Vantage GLOBAL_QUOTE から株価情報を取得
    日本株は "銘柄コード.T" 形式（例: 7203.T）で取得可能。
    """
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "GLOBAL_QUOTE",
        "symbol": symbol,
        "apikey": ALPHAVANTAGE_API_KEY,
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json().get("Global Quote", {})
        price = data.get("05. price")
        prev_close = data.get("08. previous close")
        if not price or not prev_close:
            return None

        price = float(price)
        prev_close = float(prev_close)
        change = price - prev_close
        change_pct = (change / prev_close) * 100 if prev_close != 0 else 0.0

        return {
            "price": price,
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct,
        }
    except Exception as e:
        print(f"[WARN] {symbol} の取得でエラー: {e}")
        return None


# =========================================================
# 日本株専用：指数・ランキングテキスト生成
# =========================================================

def build_index_section():
    """
    日経平均 & TOPIX の代わりに代表ETFを使用して概況を取得。
    1321.T: 日経225連動
    1306.T: TOPIX連動
    """
    indices = {
        "1321.T": "日経平均（1321.Tを指標として近似）",
        "1306.T": "TOPIX（1306.Tを指標として近似）",
    }

    lines = []
    for symbol, label in indices.items():
        q = fetch_global_quote(symbol)
        if q:
            sign = "+" if q["change"] >= 0 else ""
            lines.append(
                f"- {label}: {q['price']:.2f}円 ({sign}{q['change']:.2f}円, {sign}{q['change_pct']:.2f}%)"
            )
        else:
            lines.append(f"- {label}: データ取得失敗")

    return "\n".join(lines)


def build_stock_rankings():
    """
    代表的な日本株銘柄の中から、値上がり率/値下がり率上位を算出。
    ※ 全銘柄ではなく、主要銘柄サンプルでの簡易ランキング
    """
    # 銘柄コード: ラベル
    candidates = {
        "7203.T": "トヨタ自動車",
        "6758.T": "ソニーグループ",
        "9984.T": "ソフトバンクG",
        "9433.T": "KDDI",
        "9432.T": "日本電信電話(NTT)",
        "7974.T": "任天堂",
        "6954.T": "ファナック",
        "6762.T": "TDK",
        "6594.T": "日本電産",
        "4063.T": "信越化学工業",
        "7741.T": "HOYA",
        "8035.T": "東京エレクトロン",
        "6861.T": "キーエンス",
        "4502.T": "武田薬品工業",
        "8316.T": "三井住友FG",
        "8306.T": "三菱UFJFG",
        "8604.T": "野村ホールディングス",
        "9020.T": "JR東日本",
        "9022.T": "JR東海",
        "3382.T": "セブン＆アイHD",
    }

    results = []
    for symbol, name in candidates.items():
        q = fetch_global_quote(symbol)
        if not q:
            continue
        results.append(
            {
                "symbol": symbol,
                "name": name,
                "change_pct": q["change_pct"],
                "change": q["change"],
                "price": q["price"],
            }
        )

    if not results:
        return "個別銘柄データを取得できませんでした。"

    # 値上がり率降順 / 値下がり率昇順
    sorted_by_pct = sorted(results, key=lambda x: x["change_pct"], reverse=True)
    gainers = sorted_by_pct[:5]
    losers = sorted_by_pct[-5:]
    losers = sorted(losers, key=lambda x: x["change_pct"])  # 本当に下から順

    def fmt(entry):
        sign = "+" if entry["change_pct"] >= 0 else ""
        return f"{entry['name']} ({sign}{entry['change_pct']:.2f}%)"

    lines = []

    lines.append("■ 値上がり率 上位（主要銘柄サンプル）")
    for e in gainers:
        lines.append(f"- {fmt(e)}")

    lines.append("■ 値下がり率 上位（主要銘柄サンプル）")
    for e in losers:
        lines.append(f"- {fmt(e)}")

    # 地合い（このサンプル内での上昇/下落銘柄数）
    up = sum(1 for r in results if r["change_pct"] > 0)
    down = sum(1 for r in results if r["change_pct"] < 0)
    flat = len(results) - up - down

    lines.append(f"■ 地合い（サンプル銘柄ベース）: 上昇 {up}銘柄, 下落 {down}銘柄, 横ばい {flat}銘柄")

    return "\n".join(lines)


def build_market_snapshot_text():
    """Slackに渡す前の「生データテキスト」"""
    jst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    header = f"日本株マーケットスナップショット (JST {jst:%Y-%m-%d %H:%M:%S})"

    index_section = build_index_section()
    ranking_section = build_stock_rankings()

    return f"{header}\n\n■ 主要指数（ETF近似）\n{index_section}\n\n{ranking_section}"


# =========================================================
# GPTで日本語レポート生成
# =========================================================

def build_summary(market_snapshot: str) -> str:
    """
    上で作成した market_snapshot をもとに、
    日本株向けのマーケットサマリー文を GPT に生成させる。
    """
    prompt = f"""
以下は、本日の日本株市場に関するデータ（ETFおよび代表的な個別株のサンプル）です。

{market_snapshot}

この情報をもとに、日本株マーケットサマリーを日本語で作成してください。

要件:
- タイトル行を1行（例: 「【日本株マーケットサマリー】」）
- その下に箇条書きで3〜7行程度
- 日経平均・TOPIX（ETF近似）の動き
- 値上がり率/値下がり率上位銘柄から読み取れるテーマやセクター傾向
- サンプル銘柄数に基づく地合い（全面高 / 選別物色 / 全面安 など）のコメント
- 初心者にも分かりやすく、コンパクトに
- 最後に必ず次の一文を含める：
  「※このサマリーはAlpha Vantage等のデータを元に自動生成された参考情報であり、正確性・完全性・将来の成果を保証するものではありません。」

出力はSlackにそのまま投稿できるテキストのみ。
"""

    res = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "あなたは日本株の動向を簡潔かつ中立的に要約するアナリストです。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
    )

    return res.choices[0].message.content.strip()


# =========================================================
# Slack 投稿
# =========================================================

def post_to_slack(text: str):
    """生成したテキストを Slack に1回だけ投稿"""
    try:
        resp = slack_client.chat_postMessage(
            channel=SLACK_CHANNEL,
            text=text,
        )
        print("Slack への投稿に成功しました。ts:", resp["ts"])
    except SlackApiError as e:
        print("Slack への投稿に失敗しました:", e.response.get("error"))
        raise


def main():
    snapshot = build_market_snapshot_text()
    print("=== Market snapshot (raw) ===")
    print(snapshot)

    summary = build_summary(snapshot)
    print("=== Generated summary ===")
    print(summary)

    post_to_slack(summary)


if __name__ == "__main__":
    main()
