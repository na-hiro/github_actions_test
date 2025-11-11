#!/usr/bin/env python3
import os
import datetime
from openai import OpenAI
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# 必須環境変数チェック
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID")

assert OPENAI_API_KEY, "OPENAI_API_KEY が設定されていません"
assert SLACK_BOT_TOKEN, "SLACK_BOT_TOKEN が設定されていません"
assert SLACK_CHANNEL_ID, "SLACK_CHANNEL_ID が設定されていません"

openai_client = OpenAI(api_key=OPENAI_API_KEY)
slack_client = WebClient(token=SLACK_BOT_TOKEN)


def build_summary() -> str:
    """GPTにサンプル市場サマリを書かせる（実データにはアクセスしない）"""
    jst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    date_str = jst.strftime("%Y-%m-%d")

    prompt = f"""
今日は {date_str} です。

実際の株価APIやニュースにはアクセスせず、
一般的な傾向の例として「本日の世界株式市場サマリ（サンプル）」を日本語で作成してください。

条件:
- 日本、米国、欧州など主要市場にそれぞれ一言コメント
- 箇条書き 3〜6行程度
- あくまで例示的・仮想的な内容で、実データに基づくと誤解させない書き方
- 必ず最後に次の一文を入れる：
  「※このサマリーは自動生成されたサンプルであり、実際の市場データに基づくものではありません。」

出力は、そのままSlackに投稿できるテキストのみ。
"""

    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "あなたは簡潔で分かりやすい日本語のマーケット解説者です。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,
    )

    return response.choices[0].message.content.strip()


def post_to_slack(text: str):
    """生成したテキストをSlackに投稿"""
    try:
        slack_client.chat_postMessage(
            channel=SLACK_CHANNEL_ID,
            text=text,
        )
        print("Slack への投稿に成功しました。")
    except SlackApiError as e:
        print(f"Slack への投稿に失敗しました: {e.response['error']}")
        raise


def main():
    summary = build_summary()
    print("Generated summary:\n", summary)
    post_to_slack(summary)


if __name__ == "__main__":
    main()
