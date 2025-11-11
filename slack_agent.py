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

# 投稿先チャンネル（固定でOK）
SLACK_CHANNEL = "all-動作検証用"  # 必要なら "#all-動作検証用" に変更

openai_client = OpenAI(api_key=OPENAI_API_KEY)
slack_client = WebClient(token=SLACK_USER_TOKEN)


def fetch_quote(symbol: str):
    """Alpha Vantage の GLOBAL_QUOTE から株価情報を取得"""
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
        change = data.get("09. change")
        change_pct = data.get("10. change percent")
        if not price:
            return None
        return {
            "price": price,
            "change": change,
            "change_pct": change_pct,
        }
    except Exception as e:
        print(f"[WARN] {symbol} の取得でエラー: {e}")
        return None


def build_market_text() -> str:
    """
    代表的なETFを使って世界市場のスナップショットを作る。
    ※ ETFなので「近似的な指標」として扱う。
    """
    targets = {
        "EWJ": "日本株（EWJ）",
        "SPY": "米国株（S&P500, SPY）",
        "QQQ": "米国ハイテク（NASDAQ100, QQQ）",
        "VGK": "欧州株（VGK）",
    }

    lines = []
    for symbol, label in targets.items():
        q = fetch_quote(symbol)
        if q:
            lines.append(
                f"{label}: {q['price']} USD ({q['change']} / {q['change_pct']})"
            )
        else:
            lines.append(f"{label}: データ取得失敗")

    jst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    header = f"取得時刻 (JST): {jst:%Y-%m-%d %H:%M:%S}"
    return header + "\n" + "\n".join(lines)


def build_summary(market_text: str) -> str:
    """取得した実データをもとにGPTに日本語サマリを書かせる"""
    prompt = f"""
以下は、ETFを通じて取得した本日の市場データです（一部は代表ETFによる近似です）:

{market_text}

これをもとに、日本語で株式市場サマリーを作成してください。

要件:
- 箇条書き 3〜6行程度
- 日本株・米国株・欧州株の動きを中心に、重要なポイントを簡潔にまとめる
- 数値や方向性は上記データに基づいて説明し、勝手に別の具体的な数値を捏造しない
- 初心者にも分かりやすい表現にする
- 最後に必ず次の一文を含める：
  「※このサマリーはAlpha Vantage等のデータを元に自動生成されたものであり、正確性・完全性は保証されません。」

出力は、そのままSlackに投稿できる文章のみ。
"""

    res = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "あなたは最新データを簡潔にまとめる日本語のマーケット解説者です。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
    )

    return res.choices[0].message.content.strip()


def post_to_slack(text: str):
    """生成したテキストを Slack に 1 回だけ投稿"""
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
    market_text = build_market_text()
    print("Raw market data:\n", market_text)

    summary = build_summary(market_text)
    print("Generated summary:\n", summary)

    post_to_slack(summary)


if __name__ == "__main__":
    main()
