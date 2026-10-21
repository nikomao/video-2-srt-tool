# 视频字幕生成器（Web 版 · 向导式）

本地运行的网页应用，6 步完成视频字幕：

1. **检查环境** —— 自动检测转录引擎、NVIDIA GPU/CUDA、FFmpeg、CPU/内存/磁盘、模型、DeepSeek Key
2. **选择视频语言** —— 日语/英语/中文/韩语… 或自动检测
3. **选择字幕语言** —— 与视频相同 / 英语 / 中文 / 其他语言
4. **推荐最优模型** —— 根据显卡显存自动推荐 `large-v3 / medium / small / base` 与加速方式，可手动覆盖
5. **开始生成字幕** —— 拖入视频，自动转写 / 翻译
6. **检查字幕** —— 用 DeepSeek 校对错别字、识别错误、语句和标点，生成校对版并下载

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
└─ work/               # 任务临时目录（已忽略）
```
