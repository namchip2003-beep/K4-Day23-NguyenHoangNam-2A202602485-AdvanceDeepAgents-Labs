"""tools.py - STUDENT IMPLEMENTS.  Source tools for the research agents.   Guide: GUIDE.md, part 1.

Rules for every tool:
  * runs on the HOST (not in the sandbox): API keys must never enter the sandbox;
  * returns a STRING (JSON text of compact records) and NEVER raises:
        "NO RESULTS"  when the source answers with nothing,
        "ERROR: ..."  when the source keeps failing after the retries (the agent then tries another source);
  * the docstring is the tool description the LLM reads: keep it precise (what it does, what it returns, when to use it).
Try your tools without any agent:   python tools.py
"""
import json
import os
import time
import xml.etree.ElementTree as ET
import random
import re

import httpx
from langchain_core.tools import tool

# ---- constants (given) ----
ARXIV_URL = "https://export.arxiv.org/api/query"  # https only: http answers 301
HF_DAILY_URL = "https://huggingface.co/api/daily_papers"
HF_SEARCH_URL = "https://huggingface.co/api/papers/search"
EXA_URL = "https://mcp.exa.ai/mcp"


class RetryableError(Exception):
    """Given. Raise it inside a call to ask with_retry to wait and try again (retry_after in seconds, optional)."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


# ---- TODO 1: retry helper ----
def with_retry(fn, *, attempts=5, base=1.0, cap=30.0):
    for attempt in range(attempts):
        try:
            return fn()
        except RetryableError as e:
            if attempt == attempts - 1:
                raise
            delay = e.retry_after if e.retry_after is not None else min(base * (2 ** attempt) + random.uniform(0, 1), cap)
            time.sleep(delay)
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in (429, 500, 502, 503, 504):
                if attempt == attempts - 1:
                    raise
                raise
            if attempt == attempts - 1:
                raise
            retry_after = e.response.headers.get("Retry-After")
            delay = min(float(retry_after) if retry_after else base * (2 ** attempt) + random.uniform(0, 1), cap)
            time.sleep(delay)
        except httpx.TransportError as e:
            if attempt == attempts - 1:
                raise
            delay = min(base * (2 ** attempt) + random.uniform(0, 1), cap)
            time.sleep(delay)


_last_arxiv_call = 0

# ---- TODO 2: arXiv ----
@tool
def arxiv_search(query: str, max_results: int = 10) -> str:
    """Search arXiv papers by keywords, newest first. Returns a JSON list of {id, url, published, title, summary}."""
    global _last_arxiv_call
    try:
        terms = re.findall(r'[\w\-]+', query)
        if not terms:
            return "NO RESULTS"
        search_query = " AND ".join(f"all:{t}" for t in terms)
        max_res = max(1, min(max_results, 30))
        
        def _call_arxiv():
            global _last_arxiv_call
            now = time.time()
            if now - _last_arxiv_call < 3.0:
                time.sleep(3.0 - (now - _last_arxiv_call))
            _last_arxiv_call = time.time()
            
            resp = httpx.get(ARXIV_URL, params={
                "search_query": search_query,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": max_res
            }, timeout=30)
            resp.raise_for_status()
            return resp.text
            
        xml_data = with_retry(_call_arxiv, cap=60.0, attempts=7)
        root = ET.fromstring(xml_data)
        
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        records = []
        for entry in root.findall("atom:entry", ns):
            id_el = entry.find("atom:id", ns)
            pub_el = entry.find("atom:published", ns)
            title_el = entry.find("atom:title", ns)
            sum_el = entry.find("atom:summary", ns)
            
            if id_el is None or title_el is None:
                continue
                
            raw_id = id_el.text.split("/abs/")[-1].split("v")[0]
            title = re.sub(r'\s+', ' ', title_el.text).strip()
            summary = re.sub(r'\s+', ' ', sum_el.text).strip()[:600]
            
            records.append({
                "id": raw_id,
                "url": f"https://arxiv.org/abs/{raw_id}",
                "published": pub_el.text[:10] if pub_el is not None else "",
                "title": title,
                "summary": summary
            })
            
        if not records:
            return "NO RESULTS"
        return json.dumps(records, ensure_ascii=False)
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {str(e)}"


# ---- TODO 3: Hugging Face ----
@tool
def hf_daily_papers(limit: int = 30, date: str = "", keyword: str = "") -> str:
    """Hugging Face Daily Papers = what is trending in AI research. Returns a JSON list of
    {id, url, published, title, summary, upvotes, github, stars} sorted by upvotes. `date` is YYYY-MM-DD (empty = latest).
    `keyword` filters title/summary; there is no topic search on this endpoint (use hf_search_papers for a topic)."""
    try:
        lim = max(1, min(limit, 100))
        params = {"limit": lim}
        if date:
            params["date"] = date
            
        def _call_hf():
            resp = httpx.get(HF_DAILY_URL, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
            
        items = with_retry(_call_hf)
        records = []
        kw = keyword.lower()
        for item in items:
            paper = item.get("paper")
            if not paper or "id" not in paper:
                continue
            title = paper.get("title", "")
            summary = paper.get("summary", "")
            
            if kw and kw not in title.lower() and kw not in summary.lower():
                continue
                
            records.append({
                "id": paper["id"],
                "url": f"https://huggingface.co/papers/{paper['id']}",
                "published": paper.get("publishedAt", "")[:10] if paper.get("publishedAt") else "",
                "title": title,
                "summary": summary,
                "upvotes": paper.get("upvotes", 0),
                "github": paper.get("githubRepo", ""),
                "stars": paper.get("githubStars", 0)
            })
            
        records.sort(key=lambda x: x["upvotes"], reverse=True)
        if not records:
            return "NO RESULTS"
        return json.dumps(records, ensure_ascii=False)
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {str(e)}"

@tool
def hf_search_papers(query: str, limit: int = 10) -> str:
    """Search Hugging Face papers by topic. Returns a JSON list of
    {id, url, published, title, summary, upvotes, github, stars}."""
    try:
        lim = max(1, min(limit, 50))
        def _call_hfs():
            resp = httpx.get(HF_SEARCH_URL, params={"q": query, "limit": lim}, timeout=30)
            resp.raise_for_status()
            return resp.json()
            
        items = with_retry(_call_hfs)
        records = []
        for item in items:
            paper = item.get("paper")
            if not paper or "id" not in paper:
                continue
                
            summary = paper.get("ai_summary") or paper.get("summary", "")
            records.append({
                "id": paper["id"],
                "url": f"https://huggingface.co/papers/{paper['id']}",
                "published": paper.get("publishedAt", "")[:10] if paper.get("publishedAt") else "",
                "title": paper.get("title", ""),
                "summary": summary,
                "upvotes": paper.get("upvotes", 0),
                "github": paper.get("githubRepo", ""),
                "stars": paper.get("githubStars", 0)
            })
            
        if not records:
            return "NO RESULTS"
        return json.dumps(records, ensure_ascii=False)
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {str(e)}"


# ---- TODO 4: web search / fetch through the Exa MCP endpoint ----
def _call_exa(method_name: str, arguments: dict):
    exa_key = os.getenv("EXA_API_KEY", "")
    url = f"{EXA_URL}?exaApiKey={exa_key}" if exa_key else EXA_URL
    
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": method_name,
            "arguments": arguments
        }
    }
    
    def _do_post():
        resp = httpx.post(url, json=payload, headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        }, timeout=30)
        resp.raise_for_status()
        
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                content = line[5:].strip()
                if content:
                    data = json.loads(content)
                    if "error" in data:
                        raise Exception(f"MCP Error: {data['error']}")
                    if "result" in data:
                        meta = data["result"].get("_meta", {})
                        if meta.get("isRateLimitExceeded") or meta.get("rateLimitExceeded") or "rate limit" in str(data["result"]).lower():
                            raise RetryableError("Exa rate limit", retry_after=20)
                        
                        contents = data["result"].get("content", [])
                        return "".join(c.get("text", "") for c in contents if c.get("type") == "text")
        return ""
        
    try:
        return with_retry(_do_post, cap=60.0, attempts=7)
    except Exception as e:
        err_msg = str(e).replace(exa_key, "REDACTED") if exa_key else str(e)
        raise Exception(err_msg)

@tool
def web_search(query: str, objective: str = "", num_results: int = 5) -> str:
    """Search the web (Exa). Describe the ideal page in natural language. Returns clean text of the top results with URLs."""
    try:
        obj = objective if objective else f"Find information about {query}"
        res = _call_exa("web_search_exa", {"query": query, "objective": obj, "numResults": num_results})
        if not res or "No results" in res:
            return "NO RESULTS"
        return res
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {str(e)}"


@tool
def web_fetch(url: str) -> str:
    """Read the full content of one web page (e.g. an arXiv abstract page) as markdown. Long pages are truncated."""
    try:
        res = _call_exa("web_fetch_exa", {"urls": [url]})
        if not res:
            return "NO RESULTS"
        return res[:12000]
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {str(e)}"


# ---- TODO 5: registry (the researcher subagent gets exactly these) ----
SOURCE_TOOLS = [arxiv_search, hf_daily_papers, hf_search_papers, web_search, web_fetch]


if __name__ == "__main__":
    for name, fn, args in [
        ("arxiv_search", arxiv_search, {"query": "world model", "max_results": 3}),
        ("hf_daily_papers", hf_daily_papers, {"limit": 20}),
        ("hf_search_papers", hf_search_papers, {"query": "world model", "limit": 3}),
        ("web_search", web_search, {"query": "survey paper on world models", "num_results": 2}),
        ("web_fetch", web_fetch, {"url": "https://arxiv.org/abs/1803.10122"}),
    ]:
        try:
            print(f"== {name}\n{fn.invoke(args)[:400]}\n")
        except NotImplementedError as exc:
            print(f"== {name}: not implemented yet ({exc})\n")
