from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from translator_v2.core.models import SegmentRecord, V2Config


def format_glossary_for_prompt(glossary_payload: Dict) -> str:
    if not glossary_payload:
        return "없음"

    lines: List[str] = []
    for term, value in glossary_payload.items():
        if isinstance(value, dict):
            translation = value.get("translation", "")
            mode = value.get("mode", "translate")
            if mode == "keep":
                rendered = translation or term
                lines.append(f"- {term} -> keep as `{rendered}`")
            elif mode == "transliterate":
                lines.append(f"- {term} -> transliterate as `{translation or term}`")
            else:
                lines.append(f"- {term} -> {translation}")
        else:
            lines.append(f"- {term} -> {value}")
    return "\n".join(lines)


def build_failure_hint(segment: SegmentRecord) -> str:
    if not segment.validation_errors and not segment.warnings:
        return ""

    lines: List[str] = []
    for issue in segment.validation_errors:
        issue_type = issue.get("issue_type", "unknown")
        observed = issue.get("observed", "")
        expected = issue.get("expected", "")
        if observed and expected:
            lines.append(f"- {issue_type}: observed `{observed}` / expected `{expected}`")
        elif observed:
            lines.append(f"- {issue_type}: {observed}")
        else:
            lines.append(f"- {issue_type}")
    for warning in segment.warnings:
        lines.append(f"- warning: {warning}")
    return "\n".join(lines)


def build_batch_system_prompt(
    segments: Iterable[SegmentRecord],
    rules_text: str,
    glossary_payload: Dict,
    config: V2Config,
    failure_hints: Optional[Dict[str, str]] = None,
) -> str:
    glossary_text = format_glossary_for_prompt(glossary_payload)
    segment_ids = ", ".join(segment.segment_id for segment in segments)

    failure_section = ""
    if failure_hints:
        lines = ["[직전 검증에서 잡힌 문제]"]
        for segment_id, hint in failure_hints.items():
            if hint.strip():
                lines.append(f"{segment_id}:")
                lines.append(hint.strip())
        if len(lines) > 1:
            failure_section = "\n" + "\n".join(lines) + "\n"

    return f"""당신은 장편 문서 번역을 담당하는 전문 번역가입니다.

[목표]
- {config.source_lang} 원문을 {config.target_lang} 번역으로 옮기세요.
- 모든 세그먼트를 빠짐없이 번역하세요.
- 각 세그먼트는 지정된 wrapper 안에 정확히 한 번만 출력하세요.

[번역 규칙]
{rules_text or "없음"}

[용어 사전]
{glossary_text}
{failure_section}
[반드시 지킬 형식 규칙]
- 아래 요청에 등장하는 모든 `[[SEGMENT <id>]]`를 같은 id로 그대로 출력하세요.
- 각 세그먼트의 출력 형식은 정확히 아래와 같아야 합니다.
  [[SEGMENT <id>]]
  번역문
  [[END SEGMENT <id>]]
- wrapper 밖에 설명, 메모, 사과, 제목, 요약, 메타 코멘트를 절대 출력하지 마세요.
- 원문 문장, 숫자, 문단 정보를 임의로 생략하지 마세요.
- 번역해야 할 문장을 원어 그대로 길게 남겨두지 마세요.
- 한 세그먼트를 둘로 나누거나 여러 세그먼트를 합치지 마세요.

[이번 요청의 세그먼트]
{segment_ids}
"""


def build_batch_user_prompt(
    segments: Iterable[SegmentRecord],
    context_map: Optional[Dict[str, str]] = None,
) -> str:
    rendered: List[str] = []
    for segment in segments:
        context = ""
        if context_map and context_map.get(segment.segment_id):
            context = (
                "[참고 문맥]\n"
                f"{context_map[segment.segment_id].strip()}\n\n"
            )
        rendered.append(
            f"[[SEGMENT {segment.segment_id}]]\n"
            f"{context}[원문]\n"
            f"{segment.source_text}\n"
            f"[[END SEGMENT {segment.segment_id}]]"
        )
    return "\n\n".join(rendered)
