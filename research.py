"""research.py - STUDENT IMPLEMENTS.  The main script.   Guide: GUIDE.md, part 3.

Usage:  python research.py "survey about world model"
Result: reports/<slug>.md   reports/<slug>.sources.json   reports/<slug>.meta.json
"""
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import sys
import time  # noqa: F401
from collections import Counter  # noqa: F401
from pathlib import Path

from agents import FINALIZER_PATH, REPORT_PATH, SOURCES_PATH, VALIDATOR_PATH, WORKDIR, build_lead_agent  # noqa: F401
from model import make_model  # noqa: F401
from sandbox import download, open_sandbox, upload  # noqa: F401

ROOT = Path(__file__).parent
REPORTS = ROOT / "reports"
VALIDATOR_SOURCE = ROOT / "check_citations.py"
FINALIZER_SOURCE = ROOT / "finalize_citations.py"   # provided: uploaded next to your validator


def slugify(topic):
    """Turn a topic into a safe file name: lower case, runs of non-word characters become one "-", max 60 chars,
    never empty (fall back to "topic"). The topic is user input: "../../x" must not escape reports/."""
    slug = re.sub(r'[^\w]+', '-', topic.lower()).strip('-')
    if not slug:
        slug = "topic"
    return slug[:60].strip('-')


def build_prompt(topic):
    """The user message sent to the lead agent."""
    return f"Research the following topic and write a report: {topic}"


def summarize(messages, elapsed, model_name):
    """Return {"model", "elapsed_s", "subagent_calls", "tool_calls": {name: count}, "tokens": {"input", "output"}}."""
    tool_calls_counts = Counter()
    input_tokens = 0
    output_tokens = 0
    
    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for call in msg.tool_calls:
                tool_calls_counts[call["name"]] += 1
        
        if hasattr(msg, "usage_metadata") and msg.usage_metadata:
            meta = msg.usage_metadata
            input_tokens += meta.get("input_tokens", 0)
            output_tokens += meta.get("output_tokens", 0)
            
    return {
        "model": model_name,
        "elapsed_s": round(elapsed, 1),
        "subagent_calls": tool_calls_counts.get("task", 0),
        "tool_calls": dict(tool_calls_counts),
        "tokens": {
            "input": input_tokens,
            "output": output_tokens
        }
    }


def save_outputs(backend, topic, messages, elapsed, model_name, reports_dir=REPORTS):
    """Download the report from the sandbox and write the three files into reports_dir. Return the report path."""
    files = download(backend, [REPORT_PATH, SOURCES_PATH])
    
    report_content = files.get(REPORT_PATH)
    sources_content = files.get(SOURCES_PATH)
    
    if not report_content or not report_content.strip():
        raise RuntimeError("Report is missing or empty")
    
    if not sources_content:
        raise RuntimeError("Sources file is missing")
        
    try:
        sources_json = json.loads(sources_content.decode("utf-8"))
    except Exception:
        raise RuntimeError("Sources file is invalid JSON")
        
    slug = slugify(topic)
    reports_dir.mkdir(parents=True, exist_ok=True)
    
    source_families = sorted(list(set(s.get("source", "") for s in sources_json if s.get("source"))))
    
    meta = summarize(messages, elapsed, model_name)
    meta["topic"] = topic
    meta["n_sources"] = len(sources_json)
    meta["source_families"] = source_families
    
    (reports_dir / f"{slug}.md").write_bytes(report_content)
    (reports_dir / f"{slug}.sources.json").write_bytes(sources_content)
    (reports_dir / f"{slug}.meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    
    return reports_dir / f"{slug}.md"


def main(topic):
    """Return the process exit code (0 ok, 1 failed run, 2 no topic)."""
    topic = topic.strip()
    if not topic:
        print("Usage: python research.py <topic>", file=sys.stderr)
        return 2
        
    model = make_model()
    model_name = os.getenv("LAB_MODEL", "unknown")
    start = time.monotonic()
    
    try:
        with open_sandbox() as backend:
            backend.execute(f"mkdir -p {WORKDIR}/research/notes {WORKDIR}/report")
            
            upload(backend, {
                VALIDATOR_PATH: VALIDATOR_SOURCE.read_bytes(),
                FINALIZER_PATH: FINALIZER_SOURCE.read_bytes()
            })
            
            agent = build_lead_agent(backend, model)
            result = agent.invoke(
                {"messages": [{"role": "user", "content": build_prompt(topic)}]},
                config={"recursion_limit": 1000}
            )
            
            elapsed = time.monotonic() - start
            report_file = save_outputs(backend, topic, result["messages"], elapsed, model_name)
            print(f"Report saved to: {report_file}")
            return 0
    except RuntimeError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(" ".join(sys.argv[1:])))
