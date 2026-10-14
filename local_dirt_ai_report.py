#!/usr/bin/env python3
"""Send output/digest_latest.json to Groq and write output/report_latest.md."""

import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

API_URL = "https://api.groq.com/openai/v1/chat/completions"
DIGEST_PATH = Path("output/digest_latest.json")
REPORT_PATH = Path("output/report_latest.md")

MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

# Free tier allows ~8K tokens/minute for input + output combined,
# so the digest has to fit into one small request.
EXCERPT_CHARS = 400
MAX_INPUT_CHARS = 12000
MAX_OUTPUT_TOKENS = 2000

PROMPT = """Act as the editor of a concise Forsyth County local-news account.
Do not summarize everything. Review every item in this JSON, merge duplicate
stories, and choose only items that a meaningful number of Forsyth County residents
would care about. Prioritize major development, roads/closures, restaurants
and recognizable businesses, taxes, schools, school-board decisions, public
safety, major local government decisions, significant sports results and
unusual community news. When in doubt, skip it. For each selected item, give
me a short X/Facebook-ready post and preserve the original source URL."""


def build_input(digest: dict) -> str:
    """Compact the digest: newest items first, short excerpts, capped total size."""
    items = digest.get("items", [])  # already sorted newest first
    lines, total = [], 0
    for it in items:
        line = json.dumps({
            "source": it.get("source"),
            "title": it.get("title"),
            "url": it.get("url"),
            "published": it.get("published"),
            "text": (it.get("text") or "")[:EXCERPT_CHARS],
        }, ensure_ascii=False)
        if total + len(line) > MAX_INPUT_CHARS:
            break
        lines.append(line)
        total += len(line) + 1
    print(f"Sending {len(lines)} of {len(items)} items")
    return "Review these digest items (one JSON object per line):\n" + "\n".join(lines)


def send_request(api_key: str, payload: dict) -> dict:
    request = Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "local-dirt-workflow/1.0",
        },
        method="POST",
    )
    with urlopen(request, timeout=120) as response:
        return json.load(response)


def main() -> None:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        sys.exit("The GROQ_API_KEY repository secret is required.")

    digest = json.loads(DIGEST_PATH.read_text(encoding="utf-8"))

    payload = {
        "model": MODEL,
        "temperature": 0.3,
        "max_completion_tokens": MAX_OUTPUT_TOKENS,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": build_input(digest)},
        ],
    }
    if MODEL.startswith("openai/gpt-oss"):
        payload["reasoning_effort"] = "low"  # reasoning tokens count toward the limit

    try:
        try:
            result = send_request(api_key, payload)
        except HTTPError as e:
            if e.code != 429:
                raise
            wait = float(e.headers.get("retry-after") or 60) + 1
            print(f"Rate limited; retrying in {wait:.0f}s")
            time.sleep(wait)
            result = send_request(api_key, payload)
    except HTTPError as e:
        sys.exit(f"Groq API error {e.code}: {e.read().decode('utf-8', 'ignore')[:800]}")

    choices = result.get("choices", [])
    if not choices:
        sys.exit("Groq returned no choices: " + json.dumps(result)[:500])

    text = (choices[0].get("message", {}).get("content") or "").strip()
    if not text:
        sys.exit("Groq returned an empty report.")

    REPORT_PATH.write_text(text + "\n", encoding="utf-8")
    print(f"Wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()