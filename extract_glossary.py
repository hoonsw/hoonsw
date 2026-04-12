#!/usr/bin/env python3
"""
Glossary extraction tool.
Scans source text to extract proper nouns, key terms, and suggests translations.
"""

import asyncio
import argparse
import json
import re
from pathlib import Path
from typing import Dict, List, Set
import yaml

try:
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock
except ImportError:
    print("Error: claude-agent-sdk not installed. Run: pip install claude-agent-sdk")
    exit(1)

from translate import split_into_chunks, TranslationConfig


class ExtractionConfig:
    """Configuration for extraction (reuses config.yaml)"""
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        self.chunk_size = config.get('chunk_size', 2000)
        self.overlap_sentences = config.get('overlap_sentences', 3)
        self.source_lang = config.get('source_lang', 'ja')
        self.target_lang = config.get('target_lang', 'ko')
        self.model = config.get('model', 'claude-sonnet-4-6')
        self.max_turns = config.get('max_turns', 1)


def parse_json_response(text: str) -> list:
    """Parse JSON from AI response, stripping markdown fences if present."""
    text = text.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip().startswith("```"):
                end = i
                break
        text = "\n".join(lines[start:end])

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON array in the text
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return []


async def extract_terms_from_chunk(
    chunk_text: str,
    config: ExtractionConfig,
    existing_terms: List[str],
    max_retries: int = 3
) -> List[Dict]:
    """Extract proper nouns and key terms from a single chunk."""

    lang_name = {"ja": "일본어", "en": "영어"}.get(config.source_lang, config.source_lang)

    system_prompt = f"""You are a terminology extraction specialist.
Extract all proper nouns, names, titles, and key concept terms from the {lang_name} text.
Return ONLY a JSON array of objects, no other text.

Each object:
{{"term": "<original term as it appears in text>", "category": "<person|place|work|organization|concept>", "suggested_translation": "<Korean translation>", "annotate": <true if not widely known to Korean readers, false otherwise>}}

Already extracted terms (skip duplicates): {json.dumps(existing_terms, ensure_ascii=False)}

Rules:
- Include person names, place names, work titles (books, films, albums, songs, articles), organization names
- Include recurring technical/concept terms specific to the domain
- For 'annotate': set true for names/titles that Korean readers would not recognize without the original script
- For 'suggested_translation': provide your best Korean rendering
- Do NOT include common nouns or generic vocabulary
- Return valid JSON array only, no markdown fences or explanation"""

    user_prompt = f"Extract terms from this text:\n\n{chunk_text}"

    for attempt in range(max_retries):
        try:
            options = ClaudeAgentOptions(
                system_prompt=system_prompt,
                max_turns=config.max_turns,
                allowed_tools=[],
            )
            options.model = config.model

            result_text = ""
            async for message in query(prompt=user_prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            result_text += block.text

            return parse_json_response(result_text)

        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(f"  ⚠️  Error on attempt {attempt + 1}: {str(e)}")
                await asyncio.sleep(wait_time)
            else:
                print(f"  ❌ Failed after {max_retries} attempts: {str(e)}")
                return []


async def consolidate_terms(
    all_terms: List[Dict],
    config: ExtractionConfig
) -> List[Dict]:
    """Deduplicate and consolidate extracted terms, refine translations."""

    # Programmatic deduplication by normalized term
    term_map: Dict[str, Dict] = {}
    for t in all_terms:
        if not isinstance(t, dict) or "term" not in t:
            continue
        key = t["term"].strip()
        if key not in term_map:
            term_map[key] = {
                "term": key,
                "category": t.get("category", ""),
                "suggested_translation": t.get("suggested_translation", ""),
                "annotate": t.get("annotate", False),
                "count": 1
            }
        else:
            term_map[key]["count"] += 1

    candidates = list(term_map.values())

    if not candidates:
        return []

    # Send to AI for consolidation (merge variants, refine translations)
    system_prompt = """You are a terminology consolidation specialist for translation.
Given a list of extracted terms, you must:
1. Merge variant forms of the same entity (e.g., "東浩紀" and "アズマ" → keep "東浩紀")
2. Remove generic/common terms that don't need glossary entries
3. Refine Korean translation suggestions
4. Return a clean JSON array

Return ONLY a JSON array of objects:
{"term": "<original>", "category": "<person|place|work|organization|concept>", "suggested_translation": "<Korean>", "annotate": <boolean>, "count": <number>}

No markdown fences or explanation."""

    user_prompt = f"Consolidate these terms:\n{json.dumps(candidates, ensure_ascii=False, indent=2)}"

    try:
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            max_turns=config.max_turns,
            allowed_tools=[],
        )
        options.model = config.model

        result_text = ""
        async for message in query(prompt=user_prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        result_text += block.text

        consolidated = parse_json_response(result_text)
        if consolidated:
            return consolidated
    except Exception as e:
        print(f"  ⚠️  Consolidation failed: {e}, using raw results")

    return candidates


def write_draft_glossary(terms: List[Dict], output_path: str, existing_glossary_path: str):
    """Write draft glossary file for user review."""

    # Load existing glossary
    existing = {}
    try:
        with open(existing_glossary_path, 'r', encoding='utf-8') as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Build draft: existing entries preserved, new entries added
    draft = {}

    # Keep existing entries first
    for term, value in existing.items():
        draft[term] = value

    # Add new entries
    for term_info in terms:
        if not isinstance(term_info, dict) or "term" not in term_info:
            continue
        term = term_info["term"]
        if term in draft:
            continue  # Don't overwrite existing user-confirmed entries

        draft[term] = {
            "translation": term_info.get("suggested_translation", ""),
            "category": term_info.get("category", ""),
            "annotate": term_info.get("annotate", False),
            "count": term_info.get("count", 1),
        }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(draft, f, ensure_ascii=False, indent=2)

    return len(draft) - len(existing)  # Number of new terms


def apply_draft_to_glossary(draft_path: str, glossary_path: str = "glossary.json"):
    """Merge confirmed draft entries into the main glossary."""

    with open(draft_path, 'r', encoding='utf-8') as f:
        draft = json.load(f)

    # Load existing glossary
    glossary = {}
    try:
        with open(glossary_path, 'r', encoding='utf-8') as f:
            glossary = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    added = 0
    for term, value in draft.items():
        if term in glossary:
            continue
        if isinstance(value, dict):
            # Remove the temporary 'count' field before saving
            entry = {k: v for k, v in value.items() if k != "count"}
            glossary[term] = entry
        else:
            glossary[term] = value
        added += 1

    with open(glossary_path, 'w', encoding='utf-8') as f:
        json.dump(glossary, f, ensure_ascii=False, indent=2)

    return added


async def _extract_batch(
    batch: List[tuple],
    config: ExtractionConfig,
    known_terms: List[str],
    concurrency: int = 3,
) -> tuple:
    """Process a batch of chunks concurrently. Returns (terms, failures)."""
    semaphore = asyncio.Semaphore(concurrency)
    all_terms = []
    failures = 0
    completed = 0
    total = len(batch)

    async def process_one(idx: int, chunk: str):
        nonlocal failures, completed
        async with semaphore:
            terms = await extract_terms_from_chunk(chunk, config, known_terms)
            completed += 1
            print(f"  [{completed}/{total}] 추출 완료 ({len(terms) if terms else 0}개 용어)")
            if terms:
                all_terms.extend(terms)
            else:
                failures += 1

    tasks = [process_one(i, chunk) for i, (_, chunk) in enumerate(batch)]
    await asyncio.gather(*tasks)
    return all_terms, failures


async def extract_glossary(
    input_path: str,
    output_path: str,
    config: ExtractionConfig,
    existing_glossary_path: str = "glossary.json",
    min_count: int = 1,
    max_chunks: int = 20,
) -> None:
    """Main extraction function.

    Optimized: uses large chunks (~10K chars) and samples evenly-spaced
    chunks (default max 20) to avoid processing the entire file.
    Chunks are processed concurrently (3 at a time).
    """

    # Read input file
    print(f"📖 Reading input file: {input_path}")
    with open(input_path, 'r', encoding='utf-8') as f:
        text = f.read()

    # Build a TranslationConfig-compatible object for split_into_chunks
    # Use much larger chunks for extraction (5x translation chunk size)
    tc = TranslationConfig.__new__(TranslationConfig)
    tc.chunk_size = config.chunk_size * 5
    tc.overlap_sentences = 0  # No overlap needed for extraction
    tc.source_lang = config.source_lang
    tc.target_lang = config.target_lang

    all_chunks = split_into_chunks(text, tc)
    total_available = len(all_chunks)

    # Sample evenly-spaced chunks if there are too many
    if total_available <= max_chunks:
        sampled = all_chunks
    else:
        step = total_available / max_chunks
        indices = [min(int(i * step), total_available - 1) for i in range(max_chunks)]
        # Deduplicate while preserving order
        seen = set()
        unique_indices = []
        for idx in indices:
            if idx not in seen:
                seen.add(idx)
                unique_indices.append(idx)
        sampled = [all_chunks[i] for i in unique_indices]

    sample_count = len(sampled)
    print(f"📊 총 {total_available}개 청크 중 {sample_count}개 샘플링하여 용어 추출")

    # Phase 1: Extract terms from sampled chunks (concurrent)
    all_terms, failures = await _extract_batch(sampled, config, [], concurrency=3)

    # Collect known terms for logging
    known_terms = [t.get("term", "") for t in all_terms if isinstance(t, dict)]

    print(f"✅ 추출 완료: {len(all_terms)}개 용어 (실패: {failures}개 청크)")

    # Phase 2: Consolidate
    print("🔄 용어 정리 및 중복 제거 중...")
    consolidated = await consolidate_terms(all_terms, config)

    # Filter by min_count
    if min_count > 1:
        before = len(consolidated)
        consolidated = [t for t in consolidated if t.get("count", 1) >= min_count]
        print(f"  {before} → {len(consolidated)}개 (최소 등장 {min_count}회 필터)")

    # Phase 3: Write draft
    new_count = write_draft_glossary(consolidated, output_path, existing_glossary_path)

    print(f"\n{'='*60}")
    print(f"📋 용어 추출 결과")
    print(f"{'='*60}")
    print(f"추출 용어 수: {len(consolidated)}")
    print(f"신규 용어 수: {new_count}")
    print(f"기존 용어 유지: {len(consolidated) - new_count}")
    print(f"\n✅ 초안 저장: {output_path}")
    print(f"\n💡 다음 단계:")
    print(f"   1. {output_path} 파일을 열어 용어를 검토/수정하세요")
    print(f"   2. 불필요한 항목을 삭제하세요")
    print(f"   3. 확정 후: python3 extract_glossary.py --apply {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Extract glossary terms from source text")
    parser.add_argument("input", nargs="?", help="Input text file path")
    parser.add_argument("-o", "--output", default="glossary_draft.json",
                        help="Output draft glossary path (default: glossary_draft.json)")
    parser.add_argument("--existing", default="glossary.json",
                        help="Existing glossary to merge with (default: glossary.json)")
    parser.add_argument("--config", default="config.yaml",
                        help="Config file path (default: config.yaml)")
    parser.add_argument("--min-count", type=int, default=1,
                        help="Minimum occurrence count to include a term (default: 1)")
    parser.add_argument("--max-chunks", type=int, default=20,
                        help="Maximum number of chunks to sample (default: 20)")
    parser.add_argument("--apply", type=str, default=None, metavar="DRAFT_FILE",
                        help="Apply a draft glossary to glossary.json")

    args = parser.parse_args()

    # Apply mode
    if args.apply:
        if not Path(args.apply).exists():
            print(f"❌ Error: Draft file not found: {args.apply}")
            exit(1)
        added = apply_draft_to_glossary(args.apply, args.existing)
        print(f"✅ {added}개 용어를 {args.existing}에 반영했습니다.")
        return

    # Extract mode
    if not args.input:
        parser.error("input file is required (unless using --apply)")

    if not Path(args.input).exists():
        print(f"❌ Error: Input file not found: {args.input}")
        exit(1)

    try:
        config = ExtractionConfig(args.config)
    except FileNotFoundError:
        print(f"❌ Error: Config file not found: {args.config}")
        exit(1)
    except Exception as e:
        print(f"❌ Error loading config: {e}")
        exit(1)

    try:
        asyncio.run(extract_glossary(
            args.input, args.output, config, args.existing,
            args.min_count, args.max_chunks
        ))
    except KeyboardInterrupt:
        print("\n\n⏸️  Extraction interrupted by user")
        exit(0)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        exit(1)


if __name__ == "__main__":
    main()
