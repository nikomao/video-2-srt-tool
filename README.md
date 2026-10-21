# 视频字幕生成器（Web 版）

本地运行的网页应用：拖入视频 → 自动生成 **日语 SRT** 和 **英文 SRT** → 浏览器直接下载。

## 启动

双击 `启动字幕网页.bat`，会自动打开浏览器（默认 http://127.0.0.1:8000/）。
关闭那个黑色命令行窗口即停止服务。

## 使用

1. 把视频拖到页面虚线框（支持 mp4 / mkv / mov / avi / webm 等，可一次多个）
2. 自动上传并开始处理：
   - 第一步：生成日语字幕
   - 第二步：翻译生成英文字幕
3. 完成后点「下载日语字幕」「下载英文字幕」即可
4. 「打开文件夹」可直接在资源管理器中定位字幕

## 首次运行

- 自动下载 `faster-whisper-xxl`（约 1～2GB）到 `%LOCALAPPDATA%\AnimeSubtitleTools`
- 自动准备 `large-v3` 模型（约 3GB）：
  - 若本机 `~/.cache/huggingface` 里已有 `faster-whisper-large-v3`，会**硬链接复用，不重复下载**
  - 否则通过 `hf-mirror.com` 镜像下载
- 之后再次使用不再下载

## 输出位置

```
D:\Codes\subtitle-webapp\work\<任务ID>\Subtitles_时间\Japanese\*.srt
                                              \English\*.srt
```

上传的视频副本在任务完成后会自动删除，只保留字幕。

## 性能说明

默认使用 **CPU + int8**，稳定但较慢（large-v3 在 CPU 上转录一集动画可能要较长时间）。
如果你有 NVIDIA 显卡并已配好 CUDA，可以编辑 `app.py` 里的 `common` 参数：

```python
"--device", "cuda",
"--compute_type", "float16",
```

## 目录结构

```
subtitle-webapp/
├─ app.py              # 本地服务（Python 标准库，无需 pip install）
├─ index.html          # 网页界面
├─ 启动字幕网页.bat     # 双击启动
└─ work/               # 任务临时目录（可删）
```

## 常见问题

- **端口被占用**：程序会自动在 8000～8020 之间找空闲端口，以控制台打印的地址为准。
- **下载工具/模型很慢**：已在代码中设置 `HF_ENDPOINT=https://hf-mirror.com`；工具本身来自 GitHub，网络差时可手动把 `Faster-Whisper-XXL_r245.4_windows.7z` 放到 `%LOCALAPPDATA%\AnimeSubtitleTools\` 再启动。
- **任务失效**：刷新页面会自动恢复未完成任务；服务重启后旧任务会丢失（临时文件仍在 work 下）。
- **想清空临时文件**：点页面右上角「清理已完成」，或直接删除 `work` 目录。
