#!/usr/bin/env python3
import os
import datetime
from dotenv import load_dotenv
from langchain_community.agent_toolkits import SlackToolkit
from langchain_openai import ChatOpenAI
from langchain.agents import AgentType, initialize_agent

load_dotenv()

# 必須環境変数チェック
assert os.getenv("OPENAI_API_KEY"), "OPENAI_API_KEY が設定されていません"
assert os.getenv("SLACK_USER_TOKEN"), "SLACK_USER_TOKEN が設定されていません"

# Slackツール読み込み
toolkit = SlackToolkit()
tools = toolkit.get_tools()

# LLM設定（あなたの環境で動いていたものと同じ）
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.5,
)

# エージェント初期化
agent_executor = initialize_agent(
    tools=tools,
    llm=llm,
    agent=AgentType.STRUCTURED_CHAT_ZERO_SHOT_REACT_DESCRIPTION,
    verbose=True,
)

# 今日の日付（JST）を入れてあげる
jst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
date_str = jst.strftime("%Y-%m-%d")

# エージェントへの指示
prompt = f"""
あなたは日本語で分かりやすく解説するマーケットアナリストです。

以下の条件で、「本日の株式市場サマリー（サンプル）」を作成し、
その内容を Slack の「all-動作検証用」チャンネルに投稿してください。

条件:
- 日付: {date_str} （JSTベース）
- 日本、米国、欧州など主要市場について、それぞれ1〜2行ずつコメントする
- 箇条書き 3〜6行程度
- 実際のリアルタイムデータにはアクセスしていない前提で、
  一般的な傾向の例として自然な文章にする
- 実際の指数値・騰落率は「例」として書いてもよいが、
  本物のデータだと誤解させない書き方にする
- 最後に必ず次の一文を含める：
  「※このサマリーは自動生成されたサンプルであり、実際の市場データに基づくものではありません。」

出力は、指定チャンネルへの投稿のみを行ってください。
"""

# 実行（エージェントがSlackツールを使って投稿することを期待）
result = agent_executor.run(prompt)

print("Agent result:", result)
