# 번역 워크벤치 (Translation Workbench)

다중 모델 기반 장문 번역 워크벤치. TXT/PDF 입력을 받아 고품질 번역 초안을 생성합니다.

현재 기본 실전 번역 경로는 `translator_v3 + codex_local`입니다.
`translator_v2`와 기존 phase 2/3 풀 파이프라인은 호환/비교/디버그용으로 남아 있습니다.
`ChatGPT Pro + Codex`로 돌리고 싶다면 기본 Codex 래퍼를 그대로 쓰면 됩니다.
장기 실행은 `job_runner.py`가 납품 디렉토리 복사와 Synology 공유 링크 생성을 맡을 수 있습니다.

현재 구현 현실:
- `translator_v3`는 실전 기본 경로지만, 번역 프롬프트/백엔드 일부는 아직 v2 builder를 재사용합니다.
- runtime 외부 extractor는 현재 `docling`이 주력이고, `marker/ocrmypdf`는 확장 여지가 남아 있습니다.

권장 경로 표면:

- `source/` : 원문 입력
- `translated/` : 최종 번역 출력
- `jobs/` : 실행 상태와 결과 메타
- `delivery/` : 시놀로지 납품본

기존 `input/`, `output/`, `translator/` 경로도 계속 동작하지만, 사용자 쪽에서는 위 이름을 우선으로 쓰는 편이 더 읽기 쉽습니다.

## 주요 기능

- **TXT/PDF 입력 지원**: PDF는 텍스트 추출 우선, 실패 시 OCR 경로
- **OCR/정제 계층**: 일본어(공격적)/영어(완만) 언어별 정제
- **다중 모델 지원**: Claude/OpenAI/Gemini 설정 가능 (번역/리뷰 모델 분리)
- **구조 리뷰 + AI 리뷰 분리**: 규칙 기반 검증과 AI 의미 검증을 명확히 분리
- **청크 메타데이터(sidecar JSON)** 저장 및 정합성 검증
- **run 단위 디렉토리 구조**: runs/ 아래 실행별 메타데이터/로그 관리
- `logs/` 형태의 **실행 로그 축적**
- OpenClaw 연동
- **[Phase 2] 자동 재시도**: 문제 청크 자동 재번역 + 소분할 재시도
- **[Phase 2] 장르 프리셋**: ja_novel, ja_essay, ja_philosophy, en_fiction, en_nonfiction, en_philosophy
- **[Phase 2] 용어 승인 워크플로우**: 추출→승인→glossary 반영 파이프라인
- **[Phase 2] 진행 보고**: 단계별 보고 + OpenClaw 알림 연동
- **[Phase 3] 챕터/파트 병렬 번역**: 챕터 자동 감지 + 제한 병렬화
- **[Phase 3] 서브에이전트 후크**: 파일 기반 태스크 큐로 외부 오케스트레이터 연동
- **[Phase 3] 후검수 리포트**: 텍스트/JSON/HTML 형식의 검수 리포트 자동 생성
- **[Phase 3] Finalize 단계**: 출력 정리, 패키징, 배포 디렉토리 복사

## 설치

### 1. Python 패키지 설치
```bash
cd translator
pip install -r requirements.txt
```

### 2. OpenAI API 키 설정
```bash
export OPENAI_API_KEY=...
```

참고:
- 기본 모델은 `gpt-5.2-chat-latest`로 설정되어 있습니다.
- ChatGPT 구독과 OpenAI API 사용은 별도 과금 체계입니다.
- Claude를 계속 쓰려면 `claude-agent-sdk`를 별도로 설치하고 provider를 바꾸면 됩니다.
- API 없이 `ChatGPT Pro + Codex`를 쓰려면 아래 `codex_local` 사용법을 보세요.

### 3. Codex 로그인 확인
`codex_local external`은 로컬 `codex` CLI 로그인 상태를 사용합니다.

```bash
codex login
codex --version
```

## 사용법

### 기본 번역
```bash
python3 translate.py input.txt -o output.txt
```

위 명령은 현재 기본적으로 기존 엔트리 포인트를 타며, 실전 운영 기준 권장 경로는 아래 `translator_v3` Codex 래퍼입니다.
기존 청크 기반 번역기를 강제로 쓰려면:

```bash
python3 translate.py input.txt -o output.txt --legacy
```

### Codex 로컬 번역
OpenAI API 대신 로컬 Codex를 작업자로 붙이는 운영 경로는 `job_runner` 기준입니다.
이 경로가 큐 생성 뒤 Codex worker까지 자동으로 붙입니다.

```bash
python3 job_runner.py input.txt -o output.txt --v3 --v3-backend codex_local
```

OpenClaw/Discord 완료 알림까지 포함해 돌리려면:

```bash
./scripts/run_job_runner_codex.sh input.txt output.txt
```

위 generic Codex 래퍼는 이제 `translator_v3`를 기본으로 사용합니다.
예전 v2 경로가 필요하면:

```bash
./scripts/run_job_runner_v2_codex.sh input.txt output.txt
```

v3 기본 래퍼에서도 프리셋을 그대로 받을 수 있습니다.

```bash
./scripts/run_job_runner_codex.sh input.pdf output.txt --preset en_nonfiction
```

참고:
- PDF 입력의 기본 자동 출력 확장자는 이제 `.txt`입니다.
- `--preset en_nonfiction` 같은 요청은 v3에서 프롬프트/분할 힌트/source_lang 보정에 반영됩니다.

`job_runner.py`는 기본적으로 아래 순서로 후처리합니다.
- `TRANSLATOR_DEFAULT_DELIVER_TO`가 설정되어 있거나 `/mnt/z/home/01. 책/translator-delivery`가 존재하면 결과 패키지를 그쪽으로 복사
- Synology 공유 링크 환경변수가 설정되어 있으면 DSM File Station 공유 링크를 생성
- `job_result.json`에 `delivery_dir`, `delivered_output`, `share_url`을 기록

원하면 Codex 모델/추론 강도도 같이 넘길 수 있습니다.

```bash
./scripts/run_job_runner_codex.sh input.txt output.txt \
  --codex-model gpt-5.4 \
  --codex-reasoning-effort high
```

Synology 공유 링크 자동화를 켜려면 아래 환경변수를 설정하세요.

```bash
export SYNOLOGY_SHARE_ENABLED=1
export SYNOLOGY_BASE_URL='https://melonpanda:5001'
export SYNOLOGY_USERNAME='YOUR_USER'
export SYNOLOGY_PASSWORD='YOUR_PASSWORD'
export SYNOLOGY_MOUNT_ROOT='/mnt/z'
export TRANSLATOR_DEFAULT_DELIVER_TO='/mnt/z/home/01. 책/translator-delivery'
```

선택값:
- `SYNOLOGY_PUBLIC_BASE_URL` : 외부 공개 도메인으로 링크 주소를 치환
- `SYNOLOGY_SHARE_TARGET=delivery_dir` : 파일 대신 납품 폴더 전체를 공유
- `SYNOLOGY_SHARE_PASSWORD=...` : 공유 링크 비밀번호
- `SYNOLOGY_SHARE_EXPIRE_DAYS=7` : 만료일까지 일수
- `SYNOLOGY_VERIFY_SSL=false` : self-signed 인증서일 때만 사용

번역이 끝나면 다음 파일들이 생깁니다.
- `output.txt` → 사람 읽기용 번역문
- `output.txt.chunks.json` → 청크 메타데이터

### 이어하기 (Resume)
```bash
python3 translate.py input.txt -o output.txt
```

같은 원문 / 같은 청크 설정이면, 이미 채워진 청크를 건너뛰고 이어서 번역합니다.

### 프리셋 사용
```bash
python3 translate.py input.txt -o output.txt --preset ja_novel
```

사용 가능한 프리셋: `ja_novel`, `ja_essay`, `ja_philosophy`, `en_fiction`, `en_nonfiction`, `en_philosophy`

### 자동 재시도 포함 번역
```bash
python3 translate.py input.txt -o output.txt --auto-retry --preset ja_novel
```

번역 후 구조 리뷰를 돌려 문제 청크를 자동 재번역합니다.

### 전체 파이프라인 (번역 → 리뷰 → 재시도)
```bash
python3 run_pipeline.py input.txt -o output.txt --preset ja_novel
python3 run_pipeline.py input.txt -o output.txt --preset ja_novel --notify  # OpenClaw 알림
```

주의:
- `run_pipeline.py`는 `translator_v2`가 지원하지 않는 옵션이 붙으면 `--legacy`를 요구합니다.
- `translator_v2` 기본 사용은 아래처럼 단순 실행입니다.

```bash
python3 run_pipeline.py input.txt -o output.txt --v2
```

### [Phase 3] 병렬 번역 (챕터 단위)
```bash
# 3개 워커로 챕터별 병렬 번역
python3 run_pipeline.py input.txt -o output.txt --parallel 3 --preset ja_novel
```

챕터/파트 제목을 자동 감지하여 병렬 번역합니다. 챕터 구조가 없으면 순차 번역으로 폴백합니다.

### [Phase 3] 후검수 리포트 생성
```bash
# 파이프라인에 리포트 생성 추가
python3 run_pipeline.py input.txt -o output.txt --preset ja_novel --report

# 기존 번역에 대해 리포트만 생성
python3 finalize.py output.txt --input input.txt
```

생성 파일:
- `output.report.txt` — 사람이 읽기 쉬운 텍스트 리포트
- `output.report.json` — 구조화 JSON (자동화 연동용)
- `output.report.html` — 원문/번역 대조 보기 HTML

### [Phase 3] Finalize (출력 정리 + 패키징)
```bash
# 파이프라인 + 리포트 + finalize 한 번에
python3 run_pipeline.py input.txt -o output.txt --preset ja_novel --report --finalize

# 배포 디렉토리에 복사까지
python3 run_pipeline.py input.txt -o output.txt --report --finalize --deliver-to /path/to/delivery

# 기존 번역에 대해 finalize만
python3 finalize.py output.txt --input input.txt --deliver-to /path/to/delivery
```

Finalize가 하는 일:
1. 번역문 텍스트 정리 (불필요 공백, 과다 빈 줄 정리)
2. 일본어식 문장부호/전각 기호의 한국어 출력용 정규화 (`「」` 대사부호 변환, `『』`는 작품명/표제 후보면 `<>` 우선)
3. glossary alias 기반 표기 통일 적용
4. 후검수 리포트 생성 (txt, json, html)
5. runs/ 디렉토리에 패키징 (manifest.json 포함)
6. (선택) 배포 디렉토리에 복사

### Codex 로컬 워커로 책 번역 실행
```bash
# 출력 경로를 생략하면 입력 파일 옆에 *_ko.txt 로 생성
./scripts/run_job_runner_codex.sh /path/to/book.txt

# 출력 경로와 프리셋을 직접 지정
./scripts/run_job_runner_codex.sh /path/to/book.txt /path/to/book_ko.txt --preset en_nonfiction
```

기본 동작:
- generic Codex 래퍼는 기본적으로 `translator_v3 + codex_local` 경로를 사용
- Codex 모델/추론 강도 기본값은 `gpt-5.4` / `high`
- 번역문과 함께 `job_result.json`, `preview`, `review bundle`, `manifest/report` 성격의 아티팩트를 남겨서 OpenClaw가 후속 안내에 활용할 수 있음

주의:
- `translator_v2` 경로는 explicit compatibility/debug 용도입니다.
- v2를 강제로 쓰려면 `./scripts/run_job_runner_v2_codex.sh ...`를 사용하세요.
- `run_pipeline.py --v2` 경로는 legacy용 `--report`, `--finalize`, `--deliver-to` 옵션 제약이 여전히 있습니다.

OpenClaw/Discord에서는 workspace skill `translatebook`으로 같은 래퍼를 호출할 수 있습니다.

### [Phase 3] 서브에이전트 태스크 디스패치
```bash
# 문제 청크를 서브에이전트 태스크 큐에 등록
python3 run_pipeline.py input.txt -o output.txt --preset ja_novel --dispatch-subagents
```

파일 기반 태스크 큐 (`subagent_tasks/` 디렉토리):
- `pending/` — 대기 중 태스크 (review/rework)
- `in_progress/` — 처리 중
- `completed/` — 완료 (결과 포함)
- `failed/` — 실패

OpenClaw 또는 다른 오케스트레이터가 태스크를 claim → 처리 → complete/fail 할 수 있습니다.

### 문제 청크만 재번역
```bash
python3 translate.py input.txt -o output.txt --only-chunks 5,8,13
```

주의:
- 이 기능은 **메타데이터가 있는 산출물**에서만 안전하게 동작합니다.
- 예전의 텍스트-only 산출물은 selective retranslation을 막습니다.
- `--only-chunks`는 legacy 전용 기능입니다.

### legacy vs v2 비교 실행
```bash
python3 compare_pipelines.py input/sample.txt --output-dir compare_outputs --dry-run
```

실번역 비교:
```bash
python3 compare_pipelines.py input/sample.txt --output-dir compare_outputs
```

### 번역 검증

#### 기본 검증
```bash
python3 review.py input.txt output.txt
```

#### 청크별 상세 분석
```bash
python3 review.py input.txt output.txt --detailed
```

#### AI 검증 포함
```bash
python3 review.py input.txt output.txt --detailed --ai
```

## 로그

반복 작업 / OpenClaw 운영에 맞춰 audit 로그를 남깁니다.

- `logs/translate_audit.jsonl`
- `logs/review_audit.jsonl`
- `logs/YYYYMMDD-HHMMSS-*.log` 형태의 실행 로그

각 줄은 JSON 1개이며, 실행 시각/입력 파일/출력 파일/청크 수/문제 청크 등의 정보가 기록됩니다.

## 자동 알림 실행 (OpenClaw용)

OpenClaw에서 백그라운드 작업을 돌릴 때, 완료/실패를 채팅으로 자동 보고하려면 아래 래퍼를 사용합니다.

### 번역 + 완료 알림
```bash
./scripts/run_translate_notify.sh <input> <output>
```

예:
```bash
./scripts/run_translate_notify.sh \
  '/mnt/c/Users/hoon/Desktop/02_번역/디스코탐정수요일/disco2-jap.txt' \
  '/mnt/c/Users/hoon/Desktop/02_번역/디스코탐정수요일/disco2_translated_v2.txt'
```

Codex external 모드:
```bash
./scripts/run_translate_codex_notify.sh <input> <output>
```

### 리뷰 + 완료 알림
```bash
./scripts/run_review_notify.sh <source> <translated> --detailed
```

예:
```bash
./scripts/run_review_notify.sh \
  '/mnt/c/Users/hoon/Desktop/02_번역/디스코탐정수요일/disco2-jap.txt' \
  '/mnt/c/Users/hoon/Desktop/02_번역/디스코탐정수요일/disco2_translated_v2.txt' \
  --detailed
```

이 스크립트들은 작업 종료 후 `openclaw system event --mode now`를 호출해
완료/실패를 즉시 보고합니다.

### 파이프라인 + 완료 알림
```bash
./scripts/run_pipeline_notify.sh <input> <output> [--preset ja_novel] [--ai-review]
```

### 안정 실행 러너 (권장)
완료 알림이 채팅 경로에서 불안정할 때는 `job_runner.py`를 사용합니다.
이 러너는 실행 중 상태를 `runs/<run-id>/job_status.json`에 계속 기록하고,
끝나면 `job_result.json`을 남깁니다.

```bash
./scripts/run_job_runner.sh <input> <output> --preset ja_novel --report --finalize
```

Codex external 모드:
```bash
./scripts/run_job_runner_codex.sh <input> [output] \
  --codex-model gpt-5.4 \
  --codex-reasoning-effort high
```

생성 파일:
- `runs/<run-id>/job_status.json` — 현재 단계/상태/quality_status
- `runs/<run-id>/job_result.json` — 최종 성공/실패 요약
- `runs/<run-id>/job_stdout.log` — 전체 stdout 로그

즉 채팅 완료 보고가 누락돼도, 이 파일들로 어디까지 했는지 확실히 확인 가능합니다.

### 용어 승인 워크플로우
```bash
# 1. 용어 추출 (기존)
python3 extract_glossary.py input.txt -o glossary_draft.json

# 2. 워크플로우로 가져오기
python3 glossary_workflow.py import glossary_draft.json

# 3. 대기 중 용어 확인
python3 glossary_workflow.py pending

# 4. 개별 승인/거절
python3 glossary_workflow.py approve "用語名" --translation "번역어"
python3 glossary_workflow.py reject "不要な用語"

# 5. 전체 승인
python3 glossary_workflow.py approve-all

# 6. glossary.json에 반영
python3 glossary_workflow.py sync

# 7. 상태 확인
python3 glossary_workflow.py status
```

### PDF 번역
```bash
python3 translate.py input/document.pdf -o output/document_ko.txt
```

PDF는 자동으로 텍스트 추출 → (필요 시 OCR) → 정제 파이프라인을 거칩니다.
PyMuPDF 또는 pdfplumber가 필요합니다 (`pip install pymupdf`).

## 설정 파일 (config.yaml)

```yaml
chunk_size: 2000
overlap_sentences: 3
source_lang: "ja"
target_lang: "ko"
model: "claude-sonnet-4-6"
max_turns: 1

# 장르 프리셋 (선택)
# preset: "ja_novel"

# 자동 재시도 설정 (선택)
# retry:
#   enabled: true
#   max_retries: 2
#   score_threshold: 0.6
#   auto_split: true

# 진행 보고 (선택)
# progress:
#   notify: false
#   report_interval: 900

# 모델 분리 설정 (선택)
# providers:
#   translation:
#     provider: claude        # claude / openai / gemini
#     model: claude-sonnet-4-6
#   review:
#     provider: claude
#     model: claude-sonnet-4-6

# [Phase 3] 병렬 번역 (선택)
# parallel:
#   enabled: false
#   max_workers: 3
#   min_chapter_size: 500

# [Phase 3] 리포트 자동 생성 (선택)
# report:
#   enabled: false
#   formats: [text, json, html]

# [Phase 3] Finalize 자동 실행 (선택)
# finalize:
#   enabled: false
#   clean_text: true
#   deliver_to: ""
```

## 파일 구조

```text
translator/
├── translate.py          # 번역 엔진
├── review.py             # 리뷰 (구조 리뷰 + AI 리뷰)
├── run_pipeline.py       # 전체 파이프라인 (번역→리뷰→재시도→리포트→finalize)
├── retry_engine.py       # [Phase 2] 자동 재시도 + 소분할 재번역
├── presets.py            # [Phase 2] 장르 프리셋 (6종)
├── glossary_workflow.py  # [Phase 2] 용어 승인 워크플로우
├── progress.py           # [Phase 2] 진행 보고 / OpenClaw 알림
├── parallel.py           # [Phase 3] 챕터/파트 병렬 번역
├── subagent_hooks.py     # [Phase 3] 서브에이전트 태스크 큐 / 후크
├── report.py             # [Phase 3] 후검수 리포트 생성 (txt/json/html)
├── finalize.py           # [Phase 3] 출력 정리, 패키징, 배포
├── web.py                # Web GUI
├── extract_glossary.py   # 용어 추출
├── input_pipeline.py     # TXT/PDF 입력 파이프라인
├── normalizer.py         # OCR/텍스트 정제
├── providers.py          # 모델 추상화 (Claude/OpenAI/Gemini)
├── run_manager.py        # run/log 디렉토리 관리
├── test_phase2.py        # [Phase 2] 검증 테스트 (33개)
├── test_phase3.py        # [Phase 3] 검증 테스트 (34개)
├── config.yaml
├── rules.md
├── glossary.json
├── input/
├── output/
├── runs/                 # 실행별 메타데이터/로그
├── logs/                 # audit 로그
├── subagent_tasks/       # [Phase 3] 서브에이전트 태스크 큐
├── scripts/
└── requirements.txt
```

## 주의사항

1. **일반 터미널에서 실행**: Claude Code 세션 내부가 아니라 일반 WSL/Linux 터미널에서 실행 권장
2. **WSL 사용자**: `python` 대신 `python3` 사용 권장
3. **Max 구독 필수**: Claude Code가 로그인되어 있어야 함
4. **API 키 주의**: `ANTHROPIC_API_KEY`가 설정되어 있으면 API 과금 가능
5. **긴 시간 소요**: 책 단위 작업은 수 시간 걸릴 수 있음
6. **OCR 품질 별도**: PDF 기반 원문은 OCR 품질이 번역 품질을 크게 좌우함

## 권장 워크플로우

### 기본 (수동 재시도)
```bash
cd /home/hoonsw/dev/translator
unset ANTHROPIC_API_KEY

# 1. 청크 분석
python3 translate.py input/sample.txt --dry-run

# 2. 전체 번역
python3 translate.py input/sample.txt -o output/sample_ko.txt

# 3. 상세 리뷰
python3 review.py input/sample.txt output/sample_ko.txt --detailed

# 4. 문제 청크만 재번역
python3 translate.py input/sample.txt -o output/sample_ko.txt --only-chunks 4,9,12

# 5. 다시 리뷰
python3 review.py input/sample.txt output/sample_ko.txt --detailed
```

### Phase 2 (자동 재시도 파이프라인)
```bash
cd /home/hoonsw/dev/translator
unset ANTHROPIC_API_KEY

# 1. 용어 추출 → 승인
python3 extract_glossary.py input/sample.txt
python3 glossary_workflow.py import glossary_draft.json
python3 glossary_workflow.py approve-all
python3 glossary_workflow.py sync

# 2. 파이프라인 실행 (번역→리뷰→자동 재시도)
python3 run_pipeline.py input/sample.txt -o output/sample_ko.txt --preset ja_novel

# 3. 필요시 AI 리뷰 포함
python3 run_pipeline.py input/sample.txt -o output/sample_ko.txt --preset ja_novel --ai-review
```

### Phase 3 (병렬 + 리포트 + 패키징)
```bash
cd /home/hoonsw/dev/translator
unset ANTHROPIC_API_KEY

# 1. 용어 준비 (위와 동일)
python3 extract_glossary.py input/sample.txt
python3 glossary_workflow.py import glossary_draft.json
python3 glossary_workflow.py approve-all
python3 glossary_workflow.py sync

# 2. 풀 파이프라인: 병렬 번역 + 리뷰 + 재시도 + 리포트 + finalize
python3 run_pipeline.py input/sample.txt -o output/sample_ko.txt \
  --preset ja_novel --parallel 3 --report --finalize --notify

# 3. 배포 디렉토리에 결과물 복사까지
python3 run_pipeline.py input/sample.txt -o output/sample_ko.txt \
  --preset ja_novel --report --finalize --deliver-to /path/to/delivery

# 4. 기존 번역에 대해 리포트+finalize만
python3 finalize.py output/sample_ko.txt --input input/sample.txt
```

## 라이선스

개인 사용 목적.
