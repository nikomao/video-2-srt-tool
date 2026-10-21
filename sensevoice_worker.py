#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SenseVoice / FunASR 转录工作脚本
用法：
  python sensevoice_worker.py <video> --output <out.srt> --language zh|yue|en|ja|ko|auto --device cpu
说明：
  SenseVoiceSmall 本身不输出时间戳，这里用 FSMN-VAD 做语音分段，
  每段识别后用 VAD 的起止时间生成 SRT。
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

import soundfile as sf
from funasr import AutoModel

TAG_RE = re.compile(r"<\|.*?\|>")
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")


def clean_text(text: str) -> str:
    text = TAG_RE.sub("", text or "")
    text = EMOJI_RE.sub("", text)
    return text.strip(" \t\r\n")


def fmt_time(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def split_sentences(text: str):
    parts = re.split(r"(?<=[。！？!?；;])", text)
    return [p.strip() for p in parts if p.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--output", required=True)
    ap.add_argument("--language", default="auto")
    ap.add_argument("--device", default="cpu")
    args = ap.parse_args()

    wav = os.path.join(tempfile.gettempdir(), f"sv_{os.getpid()}.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", args.input,
         "-vn", "-ac", "1", "-ar", "16000", wav],
        check=True,
    )
    print("PROGRESS 1", flush=True)

    data, sr = sf.read(wav, dtype="float32")

    vad = AutoModel(model="fsmn-vad", device=args.device, disable_update=True)
    v = vad.generate(input=wav, max_single_segment_time=10000)
    segs = (v[0].get("value") if v else None) or [[0, int(len(data) / sr * 1000)]]
    print(f"VAD_SEGMENTS {len(segs)}", flush=True)

    kwargs = {"model": "iic/SenseVoiceSmall", "device": args.device, "disable_update": True}
    if args.language in ("zh", "yue"):
        kwargs["punc_model"] = "ct-punc"
    asr = AutoModel(**kwargs)

    cues = []
    total = len(segs)
    for idx, (start_ms, end_ms) in enumerate(segs):
        chunk = data[int(start_ms / 1000 * sr):int(end_ms / 1000 * sr)]
        if len(chunk) < sr * 0.2:
            continue
        res = asr.generate(
            input=chunk,
            language=args.language if args.language != "auto" else "auto",
            use_itn=True,
            batch_size_s=60,
        )
        text = clean_text(res[0].get("text", "") if res else "")
        if not text:
            continue
        parts = split_sentences(text) or [text]
        dur = max(0.01, (end_ms - start_ms) / 1000.0)
        step = dur / len(parts)
        for j, part in enumerate(parts):
            cs = start_ms / 1000.0 + j * step
            ce = cs + step
            cues.append((cs, ce, part))
        print(f"PROGRESS {min(99, int((idx + 1) / total * 98) + 1)}", flush=True)

    with open(args.output, "w", encoding="utf-8") as f:
        for i, (cs, ce, text) in enumerate(cues, 1):
            f.write(f"{i}\n{fmt_time(cs)} --> {fmt_time(ce)}\n{text}\n\n")

    print("PROGRESS 100", flush=True)
    print(f"DONE {len(cues)} cues -> {args.output}", flush=True)


if __name__ == "__main__":
    main()
