import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

SRC = Path('/home/hoonsw/.openclaw/workspace/translator/input/reco.txt')
DST = Path('/home/hoonsw/.openclaw/workspace/translator/output/reco_ko.txt')
STATE = DST.with_suffix(DST.suffix + '.state.json')
TMP = DST.with_suffix(DST.suffix + '.part')

MAX_BATCH_CHARS = 1000
MAX_BATCH_ITEMS = 6
SLEEP_SECONDS = 0.12
SEP_RE = re.compile(r'(\f|\n{2,})')
DELIM = '\n\ue000SEGBOUNDARY\ue001\n'


def split_long_text(text, max_chars=MAX_BATCH_CHARS):
    if len(text) <= max_chars:
        return [text]

    pieces = []
    lines = text.splitlines(keepends=True)
    buf = ''

    def flush_buf():
        nonlocal buf
        if buf:
            pieces.append(buf)
            buf = ''

    for line in lines:
        if len(line) > max_chars:
            flush_buf()
            sentence_parts = re.split(r'(?<=[.!?])\s+', line)
            tmp = ''
            for part in sentence_parts:
                if not part:
                    continue
                if len(part) > max_chars:
                    words = re.split(r'(\s+)', part)
                    hard = ''
                    for w in words:
                        if len(hard) + len(w) <= max_chars:
                            hard += w
                        else:
                            if hard:
                                pieces.append(hard)
                            hard = w
                    if hard:
                        pieces.append(hard)
                else:
                    if len(tmp) + len(part) <= max_chars:
                        tmp += part
                    else:
                        if tmp:
                            pieces.append(tmp)
                        tmp = part
            if tmp:
                pieces.append(tmp)
            continue

        if len(buf) + len(line) <= max_chars:
            buf += line
        else:
            flush_buf()
            buf = line
    flush_buf()
    return pieces


def tokenize(text):
    raw_parts = SEP_RE.split(text)
    tokens = []
    for part in raw_parts:
        if not part:
            continue
        if SEP_RE.fullmatch(part):
            tokens.append({'kind': 'sep', 'text': part})
        else:
            for sub in split_long_text(part):
                tokens.append({'kind': 'text', 'text': sub})
    return tokens


def translate_text(text, retries=6):
    url = (
        'https://translate.googleapis.com/translate_a/single?client=gtx'
        '&sl=en&tl=ko&dt=t&q=' + urllib.parse.quote(text)
    )
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    'User-Agent': 'Mozilla/5.0',
                    'Accept-Language': 'en-US,en;q=0.9'
                }
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            return ''.join(part[0] for part in data[0])
        except Exception as e:
            last_err = e
            time.sleep(min(8, 1.5 ** attempt))
    raise RuntimeError(f'translate failed after retries: {last_err}')


def load_state(total):
    if STATE.exists() and TMP.exists():
        try:
            state = json.loads(STATE.read_text(encoding='utf-8'))
            if state.get('src') == str(SRC) and state.get('total') == total:
                return state
        except Exception:
            pass
    return {'index': 0, 'total': total, 'src': str(SRC)}


def save_state(index, total):
    STATE.write_text(
        json.dumps({'index': index, 'total': total, 'src': str(SRC)}, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )


def main():
    text = SRC.read_text(encoding='utf-8')
    tokens = tokenize(text)
    total = len(tokens)
    state = load_state(total)
    index = state['index']

    mode = 'a' if index > 0 else 'w'
    os.makedirs(DST.parent, exist_ok=True)
    with open(TMP, mode, encoding='utf-8') as out:
        batch = []
        batch_start = None

        def flush_batch():
            nonlocal batch, batch_start, index
            if not batch:
                return
            joined = DELIM.join(item['text'] for item in batch)
            translated = translate_text(joined)
            parts = translated.split(DELIM)
            if len(parts) != len(batch):
                raise RuntimeError(f'delimiter split mismatch: expected {len(batch)} got {len(parts)} at token {batch_start}')
            for part in parts:
                out.write(part)
            index = batch[-1]['i'] + 1
            save_state(index, total)
            batch = []
            batch_start = None
            out.flush()
            time.sleep(SLEEP_SECONDS)

        for i in range(index, total):
            token = tokens[i]
            if token['kind'] == 'sep':
                flush_batch()
                out.write(token['text'])
                index = i + 1
                if token['text'] == '\f' or i % 200 == 0:
                    save_state(index, total)
                continue

            if batch_start is None:
                batch_start = i
            projected = sum(len(x['text']) for x in batch) + len(token['text']) + max(0, len(batch)) * len(DELIM)
            if batch and (projected > MAX_BATCH_CHARS or len(batch) >= MAX_BATCH_ITEMS):
                flush_batch()
                batch_start = i
            batch.append({'i': i, 'text': token['text']})

        flush_batch()

    TMP.replace(DST)
    if STATE.exists():
        STATE.unlink()
    print(f'WROTE {DST}')
    print(f'SIZE {DST.stat().st_size}')
    print(f'FORMFEEDS_SRC {text.count(chr(12))}')
    out_text = DST.read_text(encoding='utf-8')
    print(f'FORMFEEDS_DST {out_text.count(chr(12))}')
    print('DONE')


if __name__ == '__main__':
    main()
