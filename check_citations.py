"""check_citations.py - STUDENT IMPLEMENTS `check`.   Runs INSIDE the sandbox (standard library only).

research.py uploads this file to the sandbox and the lead agent runs it with the `execute` tool:
    python3 /tmp/work/research/check_citations.py [report.md] [sources.json]
It must exit 0 and print "OK: ..." when the report is consistent, else print each problem and exit 1.
"""
import json
import sys

REPORT = "/tmp/work/report/report.md"
SOURCES = "/tmp/work/research/sources.json"


import re

def check(report_text, sources):
    problems = []
    if not sources:
        return ["no sources in sources.json"]
        
    seen_urls = set()
    source_ns = set()
    for s in sources:
        n = s.get('n')
        if not isinstance(n, int):
            problems.append(f"source {n} has non-integer n")
            continue
        source_ns.add(n)
        
        url = s.get('url', '')
        if not (url.startswith('http://') or url.startswith('https://')):
            problems.append(f"source {n} url does not start with http(s)://")
            
        if url in seen_urls:
            problems.append(f"source {n} url is duplicated")
        seen_urls.add(url)
        
    if "## References" not in report_text:
        problems.append("missing '## References' heading")
        return problems
        
    parts = report_text.split("## References", 1)
    body = parts[0]
    ref_text = parts[1]
    
    # Remove code blocks and markdown links to avoid matching false [n]
    body_clean = re.sub(r'```.*?```', '', body, flags=re.DOTALL)
    body_clean = re.sub(r'`[^`\n]+`', '', body_clean)
    body_clean = re.sub(r'\[\d+\]\([^)]+\)', '', body_clean)
    
    cited_str = re.findall(r'\[(\d+)\]', body_clean)
    cited = set(int(x) for x in cited_str)
    
    for c in cited:
        if c not in source_ns:
            problems.append(f"[{c}] cited but missing from sources.json")
            
    for n in source_ns:
        if n not in cited:
            problems.append(f"source {n} never cited")
            
    ref_lines = [line.strip() for line in ref_text.strip().split('\n') if line.strip().startswith('[')]
    ref_n_counts = {}
    
    for line in ref_lines:
        m = re.match(r'^\[(\d+)\]', line)
        if m:
            n = int(m.group(1))
            ref_n_counts[n] = ref_n_counts.get(n, 0) + 1
            
            urls = re.findall(r'https?://[^\s)\]]+', line)
            if len(urls) != 1:
                problems.append(f"reference line [{n}] has {len(urls)} URLs, expected exactly 1")
            else:
                source_url = next((s.get('url') for s in sources if s.get('n') == n), None)
                if source_url and urls[0] != source_url:
                    problems.append(f"reference line [{n}] URL {urls[0]} does not match sources.json {source_url}")
                    
    for n in source_ns:
        count = ref_n_counts.get(n, 0)
        if count == 0:
            problems.append(f"reference line [{n}] missing")
        elif count > 1:
            problems.append(f"reference line [{n}] appears {count} times")
            
    for n in ref_n_counts:
        if n not in source_ns:
            problems.append(f"reference line [{n}] is not in sources.json")
            
    return problems


def main(argv):
    report_path = argv[1] if len(argv) > 1 else REPORT
    sources_path = argv[2] if len(argv) > 2 else SOURCES
    try:
        with open(report_path, encoding="utf-8") as f:
            report = f.read()
        with open(sources_path, encoding="utf-8") as f:
            sources = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"cannot read inputs: {exc}")
        return 1
    problems = check(report, sources)
    if problems:
        print("\n".join(problems))
        return 1
    print(f"OK: {len(sources)} sources, all citations resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
