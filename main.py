"""
경쟁사 주간 뉴스 수집 → LLM 요약 → Slack 발송
"""
import os
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from duckduckgo_search import DDGS

load_dotenv()

# 환경 변수
API_KEY = os.environ.get("LLM_API_KEY")
LLM_API_URL = os.environ.get("LLM_API_URL")  # 사내 LLM API 엔드포인트
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

# 검색 대상 키워드 (경쟁사)
KEYWORDS = ["무신사", "29CM", "W컨셉", "지그재그", "에이블리", "번개장터"]
MAX_RESULTS_PER_KEYWORD = 5
DAYS_FILTER = 7


def search_news() -> list[dict]:
    """DuckDuckGo 뉴스 검색. 키워드별 3~5개, 최근 1주일 필터."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_FILTER)
    all_articles = []

    with DDGS() as ddgs:
        for keyword in KEYWORDS:
            try:
                results = ddgs.news(
                    keywords=keyword,
                    region="kr-kr",
                    safesearch="moderate",
                    timelimit="w",
                    max_results=MAX_RESULTS_PER_KEYWORD,
                )
                for r in results:
                    date_str = r.get("date") or ""
                    try:
                        # ISO 형식 파싱 (예: 2024-07-03T16:25:22+00:00)
                        pub_dt = datetime.fromisoformat(
                            date_str.replace("Z", "+00:00")
                        )
                        if pub_dt.tzinfo is None:
                            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                    except (ValueError, TypeError):
                        pub_dt = datetime.now(timezone.utc)
                    if pub_dt >= cutoff:
                        all_articles.append(
                            {
                                "keyword": keyword,
                                "title": r.get("title", ""),
                                "body": r.get("body", ""),
                                "url": r.get("url", ""),
                                "date": date_str,
                            }
                        )
            except Exception as e:
                print(f"[검색 오류] {keyword}: {e}")
                continue

    return all_articles


def build_news_text(articles: list[dict]) -> str:
    """LLM에 넘길 뉴스 본문 텍스트 조립."""
    if not articles:
        return ""
    lines = []
    for i, a in enumerate(articles, 1):
        lines.append(
            f"[{i}] 키워드: {a['keyword']}\n제목: {a['title']}\n내용: {a['body']}\nURL: {a['url']}\n"
        )
    return "\n".join(lines)


def summarize_with_llm(news_text: str) -> str:
    """사내 LLM API(Gemini)로 '경쟁사 주간 동향' 요약."""
    if not news_text.strip():
        return "수집된 뉴스가 없어 요약을 생성하지 못했습니다."

    if not API_KEY or not LLM_API_URL:
        raise ValueError("LLM_API_KEY, LLM_API_URL 환경 변수가 필요합니다.")

    prompt = (
        "다음 뉴스들을 기업별로 분류하고 핵심 비즈니스 이슈 위주로 3줄씩 요약해줘.\n\n"
        "형식: '경쟁사 주간 동향' 리포트처럼 기업명별로 구분하고, 각 기업당 3줄 이내로 요약해줘.\n\n"
        "---\n"
        + news_text
    )

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "custom-llm-provider": "vertex_ai",
    }
    body = {
        "target_model_names": "gemini-2.5-flash",
        "messages": [{"role": "user", "content": prompt}],
    }

    resp = requests.post(
        LLM_API_URL,
        json=body,
        headers=headers,
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()

    # 응답 구조는 사내 API에 맞게 조정 필요 (예: data["choices"][0]["message"]["content"])
    if isinstance(data, dict):
        if "choices" in data and len(data["choices"]) > 0:
            msg = data["choices"][0].get("message", {})
            return msg.get("content", str(data))
        if "content" in data:
            return data["content"]
        if "message" in data:
            return data["message"]
        if "text" in data:
            return data["text"]
    return str(data)


def send_to_slack(summary: str, article_count: int) -> None:
    """Slack Incoming Webhook으로 Block Kit 포맷 전송."""
    if not SLACK_WEBHOOK_URL:
        raise ValueError("SLACK_WEBHOOK_URL 환경 변수가 필요합니다.")

    # Block Kit: 헤더 + 요약 본문 (3000자 제한 대비 분할)
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": " 경쟁사 주간 동향 ", "emoji": True},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*기간*: 최근 1주일\n*수집 기사*: {article_count}건\n*요약* (Gemini)",
            },
        },
        {"type": "divider"},
    ]

    # Slack block text 최대 3000자 제한
    chunk_size = 2900
    for i in range(0, len(summary), chunk_size):
        chunk = summary[i : i + chunk_size]
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": chunk},
            }
        )

    payload = {"blocks": blocks}
    resp = requests.post(
        SLACK_WEBHOOK_URL,
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()


def main() -> None:
    print("뉴스 검색 중...")
    articles = search_news()
    print(f"수집된 기사: {len(articles)}건")

    if not articles:
        print("수집된 뉴스가 없습니다. Slack에는 미발송합니다.")
        return

    news_text = build_news_text(articles)
    print("LLM 요약 중...")
    summary = summarize_with_llm(news_text)
    print("Slack 발송 중...")
    send_to_slack(summary, len(articles))
    print("완료.")


if __name__ == "__main__":
    main()
