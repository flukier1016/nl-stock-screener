"""合成演示视频：逐帧画面 + 逐句配音 + 低音量铺底 → H.264/AAC MP4，并做验收。

    .venv/bin/python scripts/demo_video/compose.py [--no-bed]
输出：build/demo/所以然_一句话选股_演示.mp4 与 build/demo/qc.json
验收项：时长 60–180 秒、音画时长差 < 0.5 秒、响度约 −16 LUFS、真峰值 ≤ −1 dBTP、文件 ≤ 20 MB、全片可解码。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import struct
import subprocess
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "build/demo"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
SR = 48000
FINAL = OUT / "所以然_一句话选股_演示.mp4"


def run(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"命令失败：{' '.join(cmd[:6])} …\n{p.stderr[-1500:]}")
    return p.stderr + p.stdout


def probe_duration(path: Path, stream: str) -> float:
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", stream, "-show_entries", "stream=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    return float(out.splitlines()[0]) if out else 0.0


def make_bed(path: Path, seconds: float) -> None:
    """原创铺底：A 大调和弦的几组轻微失谐正弦，慢速起伏，首尾淡入淡出。"""
    notes = [110.0, 164.81, 220.0, 277.18, 329.63]
    n = int(seconds * SR)
    frames = bytearray()
    for i in range(n):
        t = i / SR
        lfo = 0.55 + 0.45 * math.sin(2 * math.pi * 0.07 * t)
        v = sum(math.sin(2 * math.pi * f * t) + 0.6 * math.sin(2 * math.pi * (f * 1.003) * t + 1.1) for f in notes)
        fade = min(1.0, t / 3.0, (seconds - t) / 3.0)
        s = 0.018 * lfo * fade * v / len(notes)
        frames += struct.pack("<h", max(-32767, min(32767, int(s * 32767))))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(frames))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-bed", action="store_true", help="不加铺底")
    ap.add_argument("--crf", type=int, default=22)
    a = ap.parse_args()

    fr = json.loads((OUT / "frames.json").read_text(encoding="utf-8"))
    timeline = json.loads((OUT / "timeline.json").read_text(encoding="utf-8"))
    frames, t_end = fr["frames"], fr["t_end"]
    t0 = frames[0][1]
    total = t_end - t0

    # 1. 画面：按真实时间戳拼接，再统一成 30fps
    lst = OUT / "frames.txt"
    with lst.open("w", encoding="utf-8") as fh:
        for k, (name, ts) in enumerate(frames):
            nxt = frames[k + 1][1] if k + 1 < len(frames) else t_end
            fh.write(f"file 'frames/{name}'\nduration {max(nxt - ts, 0.001):.4f}\n")
        fh.write(f"file 'frames/{frames[-1][0]}'\n")
    video = OUT / "video.mp4"
    # 文件要放进 30MB 的提交压缩包：从 --crf 起逐步加大压缩，直到视频 ≤ 18MB
    for crf in range(a.crf, 33, 2):
        run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-vf", "fps=30,scale=1920:1080:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-preset", "slow",
             "-crf", str(crf), "-tune", "stillimage", "-x264-params", "keyint=90", "-movflags", "+faststart", str(video)])
        if video.stat().st_size <= 18 * 1024 * 1024:
            break
    print(f"视频 CRF {crf}，{video.stat().st_size / 1024 / 1024:.1f} MB", flush=True)

    # 2. 配音：每句放到录制时的开始时刻；可选铺底
    inputs, filters, labels = [], [], []
    for k, ln in enumerate(timeline):
        inputs += ["-i", str(OUT / "tts" / ln["file"])]
        delay = max(0, int((ln["start"] - t0) * 1000))
        filters.append(f"[{k}:a]aresample={SR},aformat=channel_layouts=mono,adelay={delay}:all=1[v{k}]")
        labels.append(f"[v{k}]")
    n = len(timeline)
    filters.append(f"{''.join(labels)}amix=inputs={n}:normalize=0:dropout_transition=0,"
                   f"loudnorm=I=-16:TP=-1.5:LRA=11,apad,atrim=0:{total:.3f}[voice]")
    out_label = "[voice]"
    if not a.no_bed:
        bed = OUT / "bed.wav"
        make_bed(bed, total)
        inputs += ["-i", str(bed)]
        filters.append(f"[{n}:a]volume=-18dB[bed]")
        filters.append("[voice][bed]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.89[mix]")
        out_label = "[mix]"
    audio = OUT / "audio.wav"
    run([FFMPEG, "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filters),
         "-map", out_label, "-ac", "2", "-ar", str(SR), str(audio)])

    # 3. 合并
    run([FFMPEG, "-y", "-loglevel", "error", "-i", str(video), "-i", str(audio), "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(FINAL)])

    # 4. 验收
    vd, ad = probe_duration(FINAL, "v:0"), probe_duration(FINAL, "a:0")
    stats = run([FFMPEG, "-hide_banner", "-nostats", "-i", str(FINAL), "-af", "ebur128=peak=true", "-f", "null", "-"])
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", stats)
    peak = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", stats)
    run([FFMPEG, "-v", "error", "-i", str(FINAL), "-f", "null", "-"])  # 全片解码
    size_mb = FINAL.stat().st_size / 1024 / 1024
    qc = {
        "file": str(FINAL.relative_to(ROOT)), "duration_video": round(vd, 2), "duration_audio": round(ad, 2),
        "lines": n, "loudness_lufs": float(lufs[-1]) if lufs else None, "true_peak_dbfs": float(peak[-1]) if peak else None,
        "size_mb": round(size_mb, 2),
    }
    qc["checks"] = {
        "时长 60–180 秒": 60 <= vd <= 180,
        "音画时长差 < 0.5 秒": abs(vd - ad) < 0.5,
        "响度 −17～−15 LUFS": qc["loudness_lufs"] is not None and -17 <= qc["loudness_lufs"] <= -15,
        "真峰值 ≤ −1 dBTP": qc["true_peak_dbfs"] is not None and qc["true_peak_dbfs"] <= -1.0,
        "文件 ≤ 20 MB": size_mb <= 20,
    }
    (OUT / "qc.json").write_text(json.dumps(qc, ensure_ascii=False, indent=1), encoding="utf-8")
    # 每句中点截一帧，供人工检查字幕和画面
    shots = OUT / "shots"
    shutil.rmtree(shots, ignore_errors=True)
    shots.mkdir()
    for ln in timeline:
        t = ln["start"] - t0 + min(ln["dur"] * 0.6, ln["dur"] - 0.3)
        run([FFMPEG, "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(FINAL), "-frames:v", "1",
             "-vf", "scale=960:-1", str(shots / f"{ln['i']:02d}.jpg")])
    print(json.dumps(qc, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
