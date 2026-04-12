#!/usr/bin/env python3
"""Web GUI for the translation tool."""

import asyncio
import builtins
import json
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from translate import (
    TranslationConfig, load_rules, load_glossary,
    split_into_chunks, translate_chunk_detailed, count_sentences,
    build_chunk_records, load_translation_artifacts,
    save_translation_artifacts, output_meta_path,
    update_record_from_translation_result,
)
from review import ReviewConfig, ReviewWriter, review_files
from extract_glossary import (
    ExtractionConfig, extract_glossary, apply_draft_to_glossary
)


@asynccontextmanager
async def lifespan(app):
    os.chdir(BASE_DIR)
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


class TaskManager:
    def __init__(self):
        self.tasks: dict[str, dict] = {}
        self._active_task_id: Optional[str] = None

    def create_task(self, task_type: str) -> str:
        if self._active_task_id and self._active_task_id in self.tasks:
            t = self.tasks[self._active_task_id]
            if t["status"] == "running":
                raise RuntimeError("이미 작업이 진행 중입니다")

        task_id = str(uuid.uuid4())[:8]
        self.tasks[task_id] = {
            "id": task_id,
            "type": task_type,
            "status": "running",
            "asyncio_task": None,
            "queue": asyncio.Queue(),
            "result": None,
        }
        self._active_task_id = task_id
        return task_id

    async def send_event(self, task_id: str, event: str, data):
        if task_id in self.tasks:
            await self.tasks[task_id]["queue"].put({"event": event, "data": data})

    def cancel_task(self, task_id: str):
        task = self.tasks.get(task_id)
        if task and task["asyncio_task"]:
            task["asyncio_task"].cancel()
            task["status"] = "cancelled"


task_manager = TaskManager()


class SSEPrintCapture:
    def __init__(self, task_id: str):
        self.task_id = task_id
        self.original_print = builtins.print
        self.loop = None

    def _patched_print(self, *args, **kwargs):
        self.original_print(*args, **kwargs)
        text = " ".join(str(a) for a in args)
        end = kwargs.get("end", "\n")
        if end == "\r":
            return
        if self.loop and self.loop.is_running():
            asyncio.ensure_future(task_manager.send_event(self.task_id, "log", text))

    async def __aenter__(self):
        self.loop = asyncio.get_running_loop()
        builtins.print = self._patched_print
        return self

    async def __aexit__(self, *exc):
        builtins.print = self.original_print


@app.get("/api/tasks/{task_id}/stream")
async def stream_task(task_id: str):
    task = task_manager.tasks.get(task_id)
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    async def event_generator():
        queue = task["queue"]
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                event = msg["event"]
                data = json.dumps(msg["data"], ensure_ascii=False)
                yield f"event: {event}\ndata: {data}\n\n"
                if event in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                yield f"event: heartbeat\ndata: \"ping\"\n\n"
            except asyncio.CancelledError:
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    task_manager.cancel_task(task_id)
    return {"status": "cancelled"}


@app.get("/api/config")
async def get_config():
    with open(BASE_DIR / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@app.put("/api/config")
async def put_config(request: Request):
    data = await request.json()
    allowed = {"chunk_size", "overlap_sentences", "source_lang", "target_lang", "model", "max_turns"}
    filtered = {k: v for k, v in data.items() if k in allowed}
    with open(BASE_DIR / "config.yaml", "w", encoding="utf-8") as f:
        yaml.dump(filtered, f, default_flow_style=False, allow_unicode=True)
    return {"status": "ok"}


@app.get("/api/files/input")
async def list_input_files():
    input_dir = BASE_DIR / "input"
    files = []
    if input_dir.exists():
        for f in sorted(input_dir.iterdir()):
            if f.is_file():
                files.append({"name": f.name, "size": f.stat().st_size})
    return files


@app.get("/api/files/output")
async def list_output_files():
    output_dir = BASE_DIR / "output"
    files = []
    if output_dir.exists():
        for f in sorted(output_dir.iterdir()):
            if f.is_file():
                item = {"name": f.name, "size": f.stat().st_size}
                meta_path = output_meta_path(str(f))
                if meta_path.exists():
                    item["meta"] = meta_path.name
                files.append(item)
    return files


@app.get("/api/glossary")
async def get_glossary():
    try:
        with open(BASE_DIR / "glossary.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


@app.put("/api/glossary")
async def put_glossary(request: Request):
    data = await request.json()
    with open(BASE_DIR / "glossary.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"status": "ok"}


@app.post("/api/glossary/apply-draft")
async def apply_draft():
    draft_path = str(BASE_DIR / "glossary_draft.json")
    glossary_path = str(BASE_DIR / "glossary.json")
    if not Path(draft_path).exists():
        return JSONResponse({"error": "glossary_draft.json not found"}, status_code=404)
    added = apply_draft_to_glossary(draft_path, glossary_path)
    return {"status": "ok", "added": added}


@app.get("/api/rules")
async def get_rules():
    try:
        with open(BASE_DIR / "rules.md", "r", encoding="utf-8") as f:
            return {"content": f.read()}
    except FileNotFoundError:
        return {"content": ""}


@app.put("/api/rules")
async def put_rules(request: Request):
    data = await request.json()
    with open(BASE_DIR / "rules.md", "w", encoding="utf-8") as f:
        f.write(data.get("content", ""))
    return {"status": "ok"}


@app.post("/api/translate/dry-run")
async def translate_dry_run(request: Request):
    data = await request.json()
    input_path = str(BASE_DIR / "input" / data["input_file"])
    config = TranslationConfig(str(BASE_DIR / "config.yaml"))

    with open(input_path, "r", encoding="utf-8") as f:
        text = f.read()

    chunks = split_into_chunks(text, config)
    result = []
    for i, (context, chunk) in enumerate(chunks, start=1):
        result.append({
            "chunk_num": i,
            "char_count": len(chunk),
            "sentence_count": count_sentences(chunk, config.source_lang),
            "chunk_preview": chunk[:200],
        })
    return {"total_chunks": len(chunks), "chunks": result}


@app.post("/api/translate/start")
async def translate_start(request: Request):
    data = await request.json()
    input_file = data["input_file"]
    output_file = data["output_file"]
    only_chunks = data.get("only_chunks")

    input_path = str(BASE_DIR / "input" / input_file)
    output_path = str(BASE_DIR / "output" / output_file)

    try:
        task_id = task_manager.create_task("translate")
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=409)

    async def run():
        async with SSEPrintCapture(task_id):
            try:
                config = TranslationConfig(str(BASE_DIR / "config.yaml"))
                rules = load_rules(str(BASE_DIR / "rules.md"))
                glossary, annotate_terms = load_glossary(str(BASE_DIR / "glossary.json"))

                with open(input_path, "r", encoding="utf-8") as f:
                    text = f.read()

                chunks = split_into_chunks(text, config)
                total = len(chunks)
                records = build_chunk_records(chunks)
                artifacts = load_translation_artifacts(output_path, input_path=input_path, config=config)

                if artifacts['ok'] and len(artifacts['records']) == total:
                    for i, rec in enumerate(artifacts['records']):
                        if rec.get('source_hash') == records[i]['source_hash']:
                            records[i]['translated_text'] = rec.get('translated_text', '')
                            records[i]['segments'] = rec.get('segments', [])
                            records[i]['validation'] = rec.get('validation', {})
                elif artifacts['reason'] == 'legacy-text-only' and not only_chunks:
                    for i, rec in enumerate(artifacts['records'][:total]):
                        records[i]['translated_text'] = rec.get('translated_text', '')

                if only_chunks:
                    if not artifacts['ok']:
                        raise RuntimeError(f"selective retranslation requires valid metadata ({artifacts['reason']})")
                    for progress_i, chunk_num in enumerate(only_chunks, start=1):
                        idx = chunk_num - 1
                        context, chunk = chunks[idx]
                        await task_manager.send_event(task_id, "progress", {"current": progress_i, "total": len(only_chunks)})
                        await task_manager.send_event(task_id, "log", f"[{progress_i}/{len(only_chunks)}] 청크 {chunk_num} 재번역 중...")

                        already_annotated = set()
                        if annotate_terms:
                            for prev_idx in range(idx):
                                prev_source = records[prev_idx]['source_text']
                                for term in annotate_terms:
                                    if term in prev_source:
                                        already_annotated.add(term)

                        translation_result = await translate_chunk_detailed(
                            chunk, context, rules, glossary, config,
                            annotate_terms=annotate_terms,
                            already_annotated=already_annotated
                        )
                        translated = translation_result['text']
                        update_record_from_translation_result(records[idx], translation_result)
                        save_translation_artifacts(output_path, records, input_path, config)

                        src_cnt = count_sentences(chunk, config.source_lang)
                        tgt_cnt = count_sentences(translated, config.target_lang)
                        await task_manager.send_event(task_id, "log", f"  ✓ 원문 {src_cnt}문장 / 번역 {tgt_cnt}문장")

                    task_manager.tasks[task_id]["status"] = "completed"
                    await task_manager.send_event(task_id, "done", "재번역 완료")
                    return

                start_chunk = 0
                while start_chunk < len(records) and records[start_chunk].get('translated_text', '').strip():
                    start_chunk += 1
                if start_chunk > 0:
                    await task_manager.send_event(task_id, "log", f"♻️ 청크 {start_chunk + 1}부터 이어서 번역")

                already_annotated = set()
                for prev_idx in range(start_chunk):
                    if annotate_terms:
                        for term in annotate_terms:
                            if term in records[prev_idx]['source_text']:
                                already_annotated.add(term)

                for i, (context, chunk) in enumerate(chunks[start_chunk:], start=start_chunk):
                    chunk_num = i + 1
                    await task_manager.send_event(task_id, "progress", {"current": chunk_num, "total": total})
                    await task_manager.send_event(task_id, "log", f"[{chunk_num}/{total}] 번역 중...")

                    translation_result = await translate_chunk_detailed(
                        chunk, context, rules, glossary, config,
                        annotate_terms=annotate_terms,
                        already_annotated=already_annotated
                    )
                    translated = translation_result['text']
                    update_record_from_translation_result(records[i], translation_result)
                    save_translation_artifacts(output_path, records, input_path, config)

                    if annotate_terms:
                        for term in annotate_terms:
                            if term in chunk:
                                already_annotated.add(term)

                    src_cnt = count_sentences(chunk, config.source_lang)
                    tgt_cnt = count_sentences(translated, config.target_lang)
                    diff = abs(src_cnt - tgt_cnt)
                    mark = "✓" if diff <= 2 else "⚠️"
                    await task_manager.send_event(task_id, "log", f"  {mark} 원문 {src_cnt}문장 / 번역 {tgt_cnt}문장")

                task_manager.tasks[task_id]["status"] = "completed"
                await task_manager.send_event(task_id, "done", "번역 완료")
            except asyncio.CancelledError:
                task_manager.tasks[task_id]["status"] = "cancelled"
                await task_manager.send_event(task_id, "done", "작업 취소됨")
            except Exception as e:
                task_manager.tasks[task_id]["status"] = "failed"
                await task_manager.send_event(task_id, "error", str(e))

    task = asyncio.create_task(run())
    task_manager.tasks[task_id]["asyncio_task"] = task
    return {"task_id": task_id}


@app.post("/api/review/start")
async def review_start(request: Request):
    data = await request.json()
    source_file = data["source_file"]
    translated_file = data["translated_file"]
    use_ai = data.get("use_ai", False)
    detailed = data.get("detailed", True)

    source_path = str(BASE_DIR / "input" / source_file)
    translated_path = str(BASE_DIR / "output" / translated_file)

    try:
        task_id = task_manager.create_task("review")
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=409)

    async def run():
        async with SSEPrintCapture(task_id):
            try:
                config = ReviewConfig(str(BASE_DIR / "config.yaml"))
                writer = ReviewWriter()
                await review_files(source_path, translated_path, config, use_ai=use_ai, detailed=detailed, writer=writer)
                task_manager.tasks[task_id]["result"] = writer.structured
                task_manager.tasks[task_id]["status"] = "completed"
                await task_manager.send_event(task_id, "result", writer.structured)
                await task_manager.send_event(task_id, "done", "리뷰 완료")
            except asyncio.CancelledError:
                task_manager.tasks[task_id]["status"] = "cancelled"
                await task_manager.send_event(task_id, "done", "작업 취소됨")
            except Exception as e:
                task_manager.tasks[task_id]["status"] = "failed"
                await task_manager.send_event(task_id, "error", str(e))

    task = asyncio.create_task(run())
    task_manager.tasks[task_id]["asyncio_task"] = task
    return {"task_id": task_id}


@app.post("/api/extract/start")
async def extract_start(request: Request):
    data = await request.json()
    input_file = data["input_file"]
    max_chunks = data.get("max_chunks", 20)

    input_path = str(BASE_DIR / "input" / input_file)
    output_path = str(BASE_DIR / "glossary_draft.json")

    try:
        task_id = task_manager.create_task("extract")
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=409)

    async def run():
        async with SSEPrintCapture(task_id):
            try:
                config = ExtractionConfig(str(BASE_DIR / "config.yaml"))
                await extract_glossary(
                    input_path, output_path, config,
                    existing_glossary_path=str(BASE_DIR / "glossary.json"),
                    max_chunks=max_chunks,
                )
                with open(output_path, "r", encoding="utf-8") as f:
                    draft = json.load(f)
                task_manager.tasks[task_id]["result"] = draft
                task_manager.tasks[task_id]["status"] = "completed"
                await task_manager.send_event(task_id, "result", draft)
                await task_manager.send_event(task_id, "done", "용어 추출 완료")
            except asyncio.CancelledError:
                task_manager.tasks[task_id]["status"] = "cancelled"
                await task_manager.send_event(task_id, "done", "작업 취소됨")
            except Exception as e:
                task_manager.tasks[task_id]["status"] = "failed"
                await task_manager.send_event(task_id, "error", str(e))

    task = asyncio.create_task(run())
    task_manager.tasks[task_id]["asyncio_task"] = task
    return {"task_id": task_id}


@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
