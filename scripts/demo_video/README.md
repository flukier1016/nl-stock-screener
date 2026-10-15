# 演示视频制作

沿用作者本地视频工作流的做法：本地配音、逐句字幕、成片验收。全部在本机完成，录制的是线上站点的真实操作。

```bash
# 1. 配音：本地 Qwen3-TTS 克隆“知识类播客”参考音色，逐句生成（改稿后只重做改动的句子）
~/Code/Qwen3-TTS/.venv/bin/python scripts/demo_video/tts_qwen.py

# 2. 录制：本机 Chrome 无界面模式，按配音节奏操作线上站点，逐帧抓 1920×1080 画面
.venv/bin/python scripts/demo_video/record.py

# 3. 合成与验收：配音按录制时刻放上音轨，加低音量铺底，统一到 −16 LUFS，压成 H.264
.venv/bin/python scripts/demo_video/compose.py
```

- 分镜与旁白在 `script.json`：`display` 是字幕，`say` 是朗读文本（数字、英文按读音写），`action` 是这句开始时执行的页面操作。
- 字幕、鼠标、点击涟漪、片头片尾都叠在页面上和画面一起录下，不依赖后期字幕滤镜。
- 每句要等“配音放完”和“页面操作完成”都满足才进入下一句，接口偶尔慢也只是画面多停一会儿，不会音画错位。
- 验收（`build/demo/qc.json`）：时长 60–180 秒、音画时长差 < 0.5 秒、响度 −17～−15 LUFS、真峰值 ≤ −1 dBTP、文件 ≤ 20 MB、全片可解码；
  另按每句中点截帧（`build/demo/shots/`）人工检查画面和字幕，关键句用 whisper-cli 回听核对读音。
- 依赖（只用于做视频，不进线上依赖）：`playwright`、本机 Google Chrome、`ffmpeg`、Qwen3-TTS 本地模型、可选 `whisper-cli`。
