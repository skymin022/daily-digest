#!/usr/bin/env python3
# 매일 도는 RSS 다이제스트 → 정적 대시보드(docs/) + Discord 웹훅 알림
# 로컬 실행 없이 GitHub Actions에서만 돌아가는 파이프라인.
#
# 동작:
#   1) FEEDS의 RSS를 모아 최근 글 수집
#   2) 이미 본 글(seen.json)과 비교해 '새 글'만 추림 (중복 알림 방지 = 멱등)
#   3) docs/index.html(대시보드) + docs/data.json 생성
#   4) 새 글을 Discord 웹훅으로 알림 (DISCORD_WEBHOOK 시크릿이 있을 때만)

import os
import json
import time
import html
import calendar
import datetime
import urllib.request

import feedparser

# ============ ✏️ 편집 영역 — 여기만 바꾸면 됨 ============
SITE_TITLE = "매일 다이제스트"

# 감시할 RSS 피드. 주제를 바꾸려면 URL만 교체/추가하면 됨.
# (전부 RSS라 스크래핑 아님 = ToS 안전. 막히는 피드는 지우고 다른 걸로 교체)
FEEDS = [
    "https://news.hada.io/rss/news",   # GeekNews (한국 개발 커뮤니티)
    "https://hnrss.org/frontpage",     # Hacker News
    "https://lobste.rs/rss",           # Lobsters
]

LOOKBACK_HOURS = 26   # '어제자' 수집 범위(여유 포함)
MAX_PER_FEED   = 15   # 피드당 최대 수집 수(한 피드가 도배하는 것 방지)
MAX_DISCORD    = 20   # 한 번에 알릴 최대 새 글 수
# =====================================================

KST = datetime.timezone(datetime.timedelta(hours=9))
SEEN_FILE = "seen.json"
DOCS_DIR = "docs"
WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()

feedparser.USER_AGENT = "daily-digest/1.0 (+github actions)"


def load_seen():
    """이전에 본 글 id 목록. 파일이 없으면 None(=최초 실행)."""
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, encoding="utf-8") as f:
                return list(json.load(f))
        except Exception:
            return []
    return None


def collect():
    """FEEDS에서 최근 LOOKBACK_HOURS 이내 글을 모아 최신순 정렬."""
    cutoff = time.time() - LOOKBACK_HOURS * 3600
    items = []
    for url in FEEDS:
        try:
            d = feedparser.parse(url)
            source = (d.feed.get("title") or url)[:60]
            count = 0
            for e in d.entries:
                if count >= MAX_PER_FEED:
                    break
                eid = e.get("id") or e.get("link")
                if not eid:
                    continue
                tp = e.get("published_parsed") or e.get("updated_parsed")
                ts = calendar.timegm(tp) if tp else time.time()
                if tp and ts < cutoff:
                    continue
                items.append({
                    "id": eid,
                    "title": (e.get("title") or "(제목 없음)").strip(),
                    "link": e.get("link") or "",
                    "source": source,
                    "ts": ts,
                })
                count += 1
            print(f"[ok] {source}: {count}건")
        except Exception as ex:
            print(f"[warn] feed 실패: {url} :: {ex}")
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items


def kst_str(ts):
    return datetime.datetime.fromtimestamp(ts, KST).strftime("%m-%d %H:%M")


TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root{
    --bg:#f7f8f8; --fg:#14181a; --muted:#67727a;
    --line:#e3e7e8; --accent:#1f6f5c;
  }
  @media (prefers-color-scheme: dark){
    :root{ --bg:#0f1214; --fg:#e7ebec; --muted:#8b959c;
           --line:#20272b; --accent:#5fd0b0; }
  }
  *{box-sizing:border-box}
  html{-webkit-text-size-adjust:100%}
  body{margin:0; background:var(--bg); color:var(--fg);
    font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans KR",sans-serif;}
  .wrap{max-width:640px; margin:0 auto; padding:32px 20px 80px}
  header{border-bottom:2px solid var(--fg); padding-bottom:14px}
  h1{margin:0; font-size:22px; font-weight:700; letter-spacing:-.01em}
  .sub{display:flex; justify-content:space-between; align-items:baseline;
       color:var(--muted); font-size:13px; margin-top:8px}
  .sub b{color:var(--accent); font-weight:700}
  ul{list-style:none; margin:0; padding:0}
  li{padding:16px 0; border-bottom:1px solid var(--line)}
  a.t{color:var(--fg); text-decoration:none; font-size:17px; font-weight:600;
      line-height:1.4; display:inline-block}
  a.t:hover{color:var(--accent); text-decoration:underline; text-underline-offset:3px}
  a.t:focus-visible{outline:2px solid var(--accent); outline-offset:3px; border-radius:2px}
  .m{display:flex; justify-content:space-between; gap:12px;
     color:var(--muted); font-size:13px; margin-top:6px}
  .src{overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
  .empty{color:var(--muted); padding:40px 0}
  footer{margin-top:40px; color:var(--muted); font-size:12px}
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>__TITLE__</h1>
      <div class="sub"><span>업데이트 __UPDATED__</span><span><b>__COUNT__</b>건</span></div>
    </header>
    <ul>
__ROWS__
    </ul>
    <footer>GitHub Actions가 매일 자동 수집합니다.</footer>
  </div>
</body>
</html>
"""


def build_dashboard(items):
    os.makedirs(DOCS_DIR, exist_ok=True)
    updated = datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    if items:
        rows = "\n".join(
            f'      <li><a class="t" href="{html.escape(it["link"])}" '
            f'target="_blank" rel="noopener">{html.escape(it["title"])}</a>'
            f'<div class="m"><span class="src">{html.escape(it["source"])}</span>'
            f'<span>{kst_str(it["ts"])}</span></div></li>'
            for it in items
        )
    else:
        rows = '      <li class="empty">새 글이 없습니다. 내일 다시 확인하세요.</li>'

    page = (TEMPLATE
            .replace("__TITLE__", html.escape(SITE_TITLE))
            .replace("__UPDATED__", updated)
            .replace("__COUNT__", str(len(items)))
            .replace("__ROWS__", rows))

    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    with open(os.path.join(DOCS_DIR, "data.json"), "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def notify_discord(new_items):
    if not WEBHOOK or not new_items:
        return
    batch = new_items[:MAX_DISCORD]
    embeds = [{
        "title": it["title"][:250],
        "url": it["link"],
        "footer": {"text": f'{it["source"]}  ·  {kst_str(it["ts"])}'},
    } for it in batch]

    # Discord는 메시지당 embed 최대 10개
    for i in range(0, len(embeds), 10):
        chunk = embeds[i:i + 10]
        payload = {"content": f"새 글 {len(chunk)}건", "embeds": chunk}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            WEBHOOK, data=data, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=30)
        except Exception as ex:
            print(f"[warn] Discord 전송 실패: {ex}")
        time.sleep(1)  # 레이트리밋 여유


def main():
    seen = load_seen()
    items = collect()
    build_dashboard(items)

    if seen is None:
        # 최초 실행: 알림 폭탄 방지 — 전부 '본 것'으로 기록만 하고 알림 생략
        ids = [it["id"] for it in items][-2000:]
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(ids, f, ensure_ascii=False)
        print(f"[init] 최초 실행: {len(ids)}건 기록, 알림 생략, 대시보드만 생성.")
        return

    seen_set = set(seen)
    new_items = [it for it in items if it["id"] not in seen_set]
    notify_discord(new_items)

    for it in items:
        if it["id"] not in seen_set:
            seen.append(it["id"])
            seen_set.add(it["id"])
    seen = seen[-2000:]  # 무한 증가 방지: 최근 2000개만 유지
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False)

    sent = "전송" if (WEBHOOK and new_items) else "생략"
    print(f"수집 {len(items)}건 / 새 글 {len(new_items)}건 / 알림 {sent}")


if __name__ == "__main__":
    main()
