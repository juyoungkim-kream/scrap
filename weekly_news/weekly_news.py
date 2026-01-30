# -*- coding: utf-8 -*-
"""
매주 월요일: 특정 키워드(예: 경쟁사) 뉴스 RSS 수집 → Gemini로 요약 → Slack 전송
환경 변수: GEMINI_API_KEY, SLACK_WEBHOOK_URL, NEWS_SEARCH_KEYWORD(검색어, 쉼표로 여러 개 가능)
"""

import os
from urllib.parse import quote_plus

import feedparser


def fetch_google_news_rss(search_keyword, max_items=15):
    """Google 뉴스 한국어 RSS에서 '검색어' 관련 기사만 가져오기."""
    # 검색어 포함, 최근 7일 기준 (주간 요약용)
    q = quote_plus(f"{search_keyword.strip()}")
    url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0"})
    items = []
    for entry in feed.entries[:max_items]:
        title = getattr(entry, "title", "") or ""
        link = getattr(entry, "link", "") or ""
        summary = getattr(entry, "summary", "") or ""
        published = getattr(entry, "published", "") or ""
        items.append({
            "title": title,
            "link": link,
            "description": summary,
            "published": published,
        })
    return items


def summarize_with_gemini(news_items, search_keywords):
    """Gemini API로 뉴스 목록 요약 (한국어). search_keywords는 리스트 또는 문자열."""
    try:
        import google.generativeai as genai
    except ImportError:
        raise SystemExit("pip install google-generativeai 후 다시 실행해 주세요.")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("환경 변수 GEMINI_API_KEY를 설정해 주세요.")

    genai.configure(api_key=api_key)

    keywords_text = ", ".join(search_keywords) if isinstance(search_keywords, list) else search_keywords

    text_block = ""
    for i, n in enumerate(news_items, 1):
        text_block += f"{i}. {n['title']}\n"
        if n.get("description"):
            text_block += f"   요약: {n['description'][:200]}...\n" if len(n.get("description", "")) > 200 else f"   요약: {n['description']}\n"
        text_block += f"   링크: {n['link']}\n\n"

    prompt = f"""아래는 '{keywords_text}' 관련 Google 뉴스(한국어) 기사 목록이에요.
각 기사의 핵심만 짧게 요약하고, 전체를 한 문단으로 정리해 주세요.
마지막에 "이번 주 주목할 뉴스" 같은 한 줄 결론을 붙여 주세요.
답변은 반드시 한국어로만 작성해 주세요.

{text_block}"""

    # 사용 가능한 모델 순서대로 시도 (환경에 따라 지원 모델이 다름)
    for model_name in ("gemini-2.0-flash", "gemini-1.5-flash", "gemini-pro"):
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            if response and response.text:
                return response.text
        except Exception as e:
            if "404" in str(e) or "not found" in str(e).lower():
                continue
            raise
    return "요약 생성 실패"


def send_to_slack(message, webhook_url=None):
    """Slack Incoming Webhook으로 메시지 전송."""
    try:
        import requests
    except ImportError:
        raise SystemExit("pip install requests 후 다시 실행해 주세요.")

    url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        raise SystemExit("환경 변수 SLACK_WEBHOOK_URL을 설정해 주세요.")

    # 메시지가 너무 길면 Slack 제한(40000자) 안으로 자르기
    payload = {
        "text": message[:39000] + ("…" if len(message) > 39000 else ""),
    }
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()


def main():
    raw = os.environ.get("NEWS_SEARCH_KEYWORD", "").strip()
    if not raw:
        raise SystemExit(
            "환경 변수 NEWS_SEARCH_KEYWORD를 설정해 주세요. "
            "예: 경쟁사 이름, 회사명 (여러 개는 쉼표로 구분)"
        )
    # 쉼표로 나눠서 여러 키워드 지원 (예: "카카오, 네이버, 토스")
    keywords = [k.strip() for k in raw.split(",") if k.strip()]

    all_items = []
    seen_links = set()
    per_keyword = 10  # 키워드당 가져올 기사 수

    for keyword in keywords:
        print(f"뉴스 수집 중... ({keyword})")
        items = fetch_google_news_rss(search_keyword=keyword, max_items=per_keyword)
        for item in items:
            link = item.get("link", "")
            if link and link not in seen_links:
                seen_links.add(link)
                all_items.append(item)

    if not all_items:
        print("수집된 뉴스가 없습니다. 검색어를 바꿔 보세요.")
        return

    # 요약용으로 최대 25개까지 (너무 많으면 토큰 초과 방지)
    all_items = all_items[:25]

    print("Gemini로 요약 중...")
    summary = summarize_with_gemini(all_items, search_keywords=keywords)

    print("Slack으로 전송 중...")
    send_to_slack(summary)
    print("완료: Slack 채널에 요약이 전송되었습니다.")


if __name__ == "__main__":
    main()
