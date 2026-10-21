# 视频字幕生成器（Web 版 · 向导式）

本地运行的网页应用，6 步生成字幕 + 历史记录 + 平台发布文案：

1. **检查环境** —— 自动检测转录引擎、NVIDIA GPU/CUDA、FFmpeg、CPU/内存/磁盘、模型、DeepSeek Key
2. **选择视频语言** —— 日语/英语/中文/韩语… 或自动检测
3. **选择字幕语言** —— 与视频相同 / 英语 / 中文 / 其他语言
4. **推荐最优模型** —— 根据显卡显存自动推荐 `large-v3 / medium / small / base` 与加速方式，可手动覆盖
5. **开始生成字幕** —— 拖入视频，自动转写 / 翻译
6. **检查字幕** —— 用 DeepSeek 校对错别字、识别错误、语句和标点，生成校对版并下载
7. **历史记录** —— 所有上传过的视频和字幕永久保留，可下载 / 继续生成 / 校对 / 生成平台文案 / 删除

### 平台发布文案（DeepSeek）

在「历史记录」里对任意已完成字幕点 **「✍ 生成平台文案」**，会根据字幕内容生成英文的：

- YouTube（标题 ≤100 字符 + 简介 + hashtags）
- TikTok（钩子 caption + hashtags）
- Instagram Reels
- X (Twitter)（≤280 字符）
- Facebook
- Bilibili

每个平台一张卡片，点「复制」即可直接粘贴发布。

## ⑧ 平台视频规格检查与优化

上传剪辑完成的成片，自动用 ffprobe 检查：

- 容器 / 视频编码 / 音频编码
- 分辨率 / 帧率 / 像素格式（8-bit 4:2:0）
- 时长 / 文件大小 / 码率 / faststart
- 可变帧率（VFR）

并按平台规格给出「✅ 符合 / 🔧 需无损封装 / ❌ 需转码」和具体问题。不符合时可以点「🤖 DeepSeek 深度检查」，让 DeepSeek 根据 ffprobe 信息给出问题、修复建议和 ffmpeg 参数。

点「优化并生成」会自动判断：

- **无损封装**：编码/分辨率/帧率/音频已合规，只做 `-c copy -movflags +faststart -avoid_negative_ts make_zero`，画质零损失、秒级完成
- **重新编码**：需要改编码/分辨率/帧率/像素格式时先转码，**最后再做一次无损封装**

具体规格：

- **Reddit**：H.264 High + AAC 48kHz + yuv420p + 恒定帧率 + faststart，≤1080p / ≤15 分钟 / ≤1GB；分辨率低于 720p 会自动升到 720p（解决 Reddit 上传处理失败）
- **TikTok / Instagram Reels**：竖屏 1080×1920（等比缩放 + 黑边）
- **YouTube / Facebook / Bilibili / X**：按各自上限缩放

输出：`video-work\<任务>\optimized\<原名>_<平台>.mp4`

## 模型选择（按语言自动推荐）

应用会根据「视频语言 + 质量/均衡/速度」自动选模型，并说明原因：

| 语言 | 质量优先 | 均衡 | 速度优先 |
|---|---|---|---|
| 中文（普通话） | large-v3 | large-v3-turbo | medium |
| 粤语 | large-v3 | large-v3-turbo | medium |
| 英语 | large-v3 | distil-large-v3 | small / medium |
| 日语 | large-v3 | large-v3-turbo | medium |
| 韩语 | large-v3 | large-v3-turbo | medium |
| 德语 | large-v3 | large-v3-turbo | medium |

说明：

- **large-v3**：官方多语言质量最佳，中文/粤语/日语/韩语/德语首选
- **large-v3-turbo**：large-v3 的蒸馏加速版，速度快 6-8 倍，质量略低
- **distil-large-v3**：英语专用，速度快约 6 倍，英语质量接近 large-v3
- **medium / small / base / tiny**：逐级更快、质量更低
- **粤语**：Whisper 对粤语支持有限，建议 large-v3 并填写专有名词
- **日语**：建议填写作品名/角色名，可显著减少同音词错误（如 買って/勝って）

已下载的模型位于 `%LOCALAPPDATA%\AnimeSubtitleTools\models`，可选：
`large-v3`、`large-v3-turbo`、`distil-large-v3`、`medium`、`small`、`base`、`tiny`。

## 启动

- 双击桌面的 **「视频字幕生成器」** 图标，或
- 双击 `启动字幕网页.bat`，或
- 命令行：`python app.py`

浏览器会自动打开 `http://127.0.0.1:8000/`。关闭黑色命令行窗口即停止服务。

## 语言与翻译规则

| 你选择的字幕语言 | 处理方式 |
|---|---|
| 与视频相同 / 与源语言一致 | faster-whisper 直接转写 |
| 英语 | faster-whisper 内置 `--task translate`（无需 API Key） |
| 其他语言（中文等） | 先转写，再用 DeepSeek 翻译（需要 API Key） |

## DeepSeek API Key

用于两件事：
- 把字幕翻译成英语以外的目标语言
- **字幕校对**（第 6 步）

填写位置：第 ④ 步页面下方「DeepSeek API Key」→ 保存。
Key 只保存在本机 `config.json`，该文件已在 `.gitignore` 中，不会上传到 GitHub。
也可用环境变量 `DEEPSEEK_API_KEY`。

## 首次运行

- 自动下载 `faster-whisper-xxl`（约 1～2GB）到 `%LOCALAPPDATA%\AnimeSubtitleTools`
- 自动准备模型：
  - 若本机 `~/.cache/huggingface` 已有 `faster-whisper-large-v3`，会**硬链接复用**
  - 否则通过 `hf-mirror.com` 镜像下载
- 之后不再下载

## 输出

```
D:\Codes\subtitle-webapp\work\<任务ID>\
├─ Subtitles_时间\Source\        # 原语言转写
├─ Subtitles_时间\Subtitles\     # 目标语言字幕
└─ Subtitles_reviewed\           # DeepSeek 校对版
```

页面第 ⑥ 步可直接下载字幕 / 原语言 / 校对版，或点「📂 文件夹」定位。

## GPU 加速

第 ④ 步会自动判断：
- 显存 ≥ 6GB：`cuda + float16`
- 显存 3.5–6GB：`cuda + int8_float16`
- 无 GPU 或显存不足：`cpu + int8`

若 CUDA 运行失败，会自动回退到 CPU 重试。

## 目录结构

```
subtitle-webapp/
├─ app.py              # 本地服务（Python 标准库）
├─ index.html          # 向导式网页
├─ icon.ico            # 桌面图标
├─ 启动字幕网页.bat     # 启动脚本
├─ config.json         # 本地配置（自动生成，已忽略）
├─ history.json        # 历史记录（自动生成，已忽略）
└─ work/               # 任务临时目录（已忽略）
```
