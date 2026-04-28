# 번역 워크벤치 (translator_v3)

장문 번역 워크벤치의 현재 지원 표면은 `translator_v3` 하나입니다.

현재 상태:
- 운영 기본 경로는 `job_runner.py -> translator_v3.cli`입니다.
- `translate.py`는 리뷰/리포트/finalize용 shared helper module로만 남아 있고, CLI는 retired 상태입니다.
- `run_pipeline.py`와 `web.py`는 retired shim입니다.
- `translator_v2/` 코드는 일부 프롬프트/검증/worker 재사용 때문에 내부적으로만 남아 있습니다. 현재 지원되는 실행 표면은 아닙니다.

## 빠른 시작

### 설치

```bash
cd /home/hoonsw/dev/translator
pip install -r requirements.txt
```

OpenAI API를 쓸 경우:

```bash
export OPENAI_API_KEY=...
```

로컬 Codex를 쓸 경우:

```bash
codex login
codex --version
```

### 기본 실행

운영 기본:

```bash
python3 job_runner.py input.txt -o output.txt --v3-backend codex_local
```

직접 v3 CLI를 쓰려면:

```bash
python3 -m translator_v3.cli run input.txt -o output.txt --backend codex_local --model gpt-5.4 --reasoning-effort high
```

Codex 래퍼:

```bash
./scripts/run_job_runner_codex.sh input.txt output.txt
./scripts/launch_job_runner_codex.sh input.txt output.txt
```

프리셋도 그대로 지원합니다.

```bash
./scripts/run_job_runner_codex.sh input.pdf output.txt --preset en_nonfiction
```

## 결과물

`job_runner.py`는 실행별로 `runs/<run-id>/` 아래에 상태와 결과를 남깁니다.

- `job_status.json`: 현재 단계, 진행 상태, live artifact pointer
- `job_result.json`: 최종 성공/실패/blocked 요약
- `job_stdout.log`: 전체 stdout 로그
- `v3_artifacts/`: preview, review bundle, telemetry, translated output bundle

성공 시에는 `TRANSLATOR_DEFAULT_DELIVER_TO` 또는 기본 납품 디렉토리가 있으면 결과 패키지를 복사하고, Synology 공유 링크 환경변수가 잡혀 있으면 링크도 생성합니다.

## 보조 도구

현재도 유효한 보조 CLI:

```bash
python3 review.py input.txt output.txt
python3 finalize.py output.txt --input input.txt
python3 extract_glossary.py input.txt -o glossary_draft.json
python3 glossary_workflow.py status
```

이 도구들은 `translate.py`에 남아 있는 shared config/chunk helper를 사용하지만, 번역 실행 경로 자체는 `translator_v3`만 지원합니다.

## 스크립트 정리

지원 표면:

- `job_runner.py`
- `python3 -m translator_v3.cli ...`
- `scripts/run_job_runner_codex.sh`
- `scripts/run_job_runner_v3_codex.sh`
- `scripts/launch_job_runner_codex.sh`
- `scripts/launch_job_runner_v3_codex.sh`

마이그레이션 shim:

- `scripts/run_translate_notify.sh`
- `scripts/run_pipeline_notify.sh`
- `scripts/run_job_runner_v2_codex.sh`
- `scripts/launch_job_runner_v2_codex.sh`

이 shim들은 더 이상 legacy/v2 경로를 실행하지 않고, 모두 v3 경로로 포워딩합니다.

## 설정

[config.yaml](/home/hoonsw/dev/translator/config.yaml:1)은 이제 주로 리뷰/finalize/glossary 보조 도구가 참고하는 shared default 파일입니다.
`translator_v3` 본 실행은 `translator_v3.cli`와 `job_runner.py` 인자로 구동됩니다.

현재 기본 설정 예시:

```yaml
chunk_size: 2000
overlap_sentences: 3
source_lang: "ja"
target_lang: "ko"
provider: "openai"
model: "gpt-5.2-chat-latest"
max_turns: 1
```

## 테스트

현재 유지 기준 테스트:

```bash
python3 test_translator_v3.py
python3 test_job_runner.py
python3 test_translate_entrypoint.py
python3 test_input_pipeline.py
python3 test_readability_gate.py
```

## 문서

- [docs/translator_v3/README.md](/home/hoonsw/dev/translator/docs/translator_v3/README.md:1)
- [docs/translator_v3/quality-path.md](/home/hoonsw/dev/translator/docs/translator_v3/quality-path.md:1)
- [docs/plans/active/translator-quality.md](/home/hoonsw/dev/translator/docs/plans/active/translator-quality.md:1)

역사 문서로는 `docs/translator_v2/`가 남아 있지만, 현재 지원 경로 설명은 `translator_v3` 문서를 기준으로 보면 됩니다.

## 파일 구조

```text
translator/
├── job_runner.py
├── translator_v3/
├── review.py
├── report.py
├── finalize.py
├── extract_glossary.py
├── glossary_workflow.py
├── config.yaml
├── runs/
├── runs_v3/
├── scripts/
└── docs/
```

## 라이선스

개인 사용 목적.
