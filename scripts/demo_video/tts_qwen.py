"""逐句生成旁白：本地 Qwen3-TTS，克隆“知识类播客”参考音色（与作者视频工作流的试音一致）。

需要用 Qwen3-TTS 的环境运行：
    ~/Code/Qwen3-TTS/.venv/bin/python scripts/demo_video/tts_qwen.py
输出：build/demo/tts/NN.wav（去掉首尾静音）与 build/demo/tts/lines.json（每句时长）。
同一句文本已生成过就跳过，改稿后只重做改动的句子。
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel

ROOT = Path(__file__).resolve().parents[2]
QWEN = Path.home() / "Code/Qwen3-TTS"
MODEL = QWEN / "models/Qwen3-TTS-12Hz-1.7B-Base"
REF_WAV = QWEN / "voice_ab/ref_M3_播客.wav"
REF_TEXT = "这件事我琢磨了挺久。一开始觉得很简单，后来越想越不对劲。今天就慢慢跟你说说，我是怎么一步一步想明白的。"
OUT = ROOT / "build/demo/tts"


def trim(w: np.ndarray, sr: int, db: float = -42.0, pad: float = 0.06) -> np.ndarray:
    thr = 10 ** (db / 20) * (np.abs(w).max() or 1.0)
    idx = np.where(np.abs(w) > thr)[0]
    if not len(idx):
        return w
    a = max(0, idx[0] - int(pad * sr))
    b = min(len(w), idx[-1] + int(pad * sr))
    return w[a:b]


def main() -> None:
    script = json.loads((ROOT / "scripts/demo_video/script.json").read_text(encoding="utf-8"))
    lines = [(s["id"], s.get("chapter", ""), ln) for s in script["scenes"] for ln in s["lines"]]
    OUT.mkdir(parents=True, exist_ok=True)
    todo = []
    meta = []
    for i, (sid, chap, ln) in enumerate(lines):
        key = hashlib.sha1(ln["say"].encode()).hexdigest()[:10]
        f = OUT / f"{i:02d}_{key}.wav"
        meta.append({"i": i, "scene": sid, "chapter": chap, "display": ln["display"], "say": ln["say"],
                     "action": ln.get("action", "none"), "file": f.name})
        if not f.exists():
            todo.append((i, ln["say"], f))
    if todo:
        t0 = time.time()
        kw = dict(device_map="mps", dtype=torch.bfloat16, attn_implementation="sdpa")
        model = Qwen3TTSModel.from_pretrained(str(MODEL), **kw)
        ref, rsr = sf.read(str(REF_WAV))
        prompt = model.create_voice_clone_prompt(ref_audio=(ref, rsr), ref_text=REF_TEXT)
        print(f"模型就绪 {time.time() - t0:.0f}s，待生成 {len(todo)} 句", flush=True)
        for i, say, f in todo:
            t1 = time.time()
            o, sr = model.generate_voice_clone(text=say, language="Chinese", voice_clone_prompt=prompt)
            w = trim(np.asarray(o[0], dtype=np.float32), sr)
            sf.write(str(f), w, sr)
            print(f"  {i:02d} {len(w) / sr:5.2f}s  用时 {time.time() - t1:4.1f}s  {say[:24]}", flush=True)
    for m in meta:
        info = sf.info(str(OUT / m["file"]))
        m["dur"] = round(info.frames / info.samplerate, 3)
    (OUT / "lines.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(m["dur"] for m in meta)
    print(f"共 {len(meta)} 句，旁白合计 {total:.1f}s", flush=True)


if __name__ == "__main__":
    sys.exit(main())
