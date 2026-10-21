#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频字幕生成 Web 应用（本地运行）— 向导式流程
============================================================
流程：
  1) 检查环境            /api/env
  2) 选择视频语言
  3) 选择字幕语言
  4) 推荐最优模型         /api/recommend
  5) 上传视频并生成字幕    /api/upload + /api/start
  6) 检查字幕（DeepSeek） /api/review

仅使用 Python 标准库，无需 pip install。
启动：双击「启动字幕网页.bat」
"""

import codecs
import ctypes
import importlib.util
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------------------------------------------------------- 路径 / 常量
APP_DIR = Path(__file__).resolve().parent
WORK_DIR = APP_DIR / "work"
WORK_DIR.mkdir(parents=True, exist_ok=True)
SENSEVOICE_WORKER = APP_DIR / "sensevoice_worker.py"
CONFIG_PATH = APP_DIR / "config.json"
HISTORY_PATH = APP_DIR / "history.json"

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
TOOL_DIR = LOCALAPPDATA / "AnimeSubtitleTools"
TOOL_DIR.mkdir(parents=True, exist_ok=True)
MODEL_DIR = TOOL_DIR / "models"

TOOL_ARCHIVE_URL = (
    "https://github.com/Purfview/whisper-standalone-win/releases/download/"
    "Faster-Whisper-XXL/Faster-Whisper-XXL_r245.4_windows.7z"
)
SEVENZIP_URL = "https://www.7-zip.org/a/7zr.exe"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
QWEN_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

HOST = "127.0.0.1"
START_PORT = 8000
END_PORT = 8020
MAX_LOG = 600
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
CREATE_NO_WINDOW = 0x08000000

LANGUAGES = {
    "auto": "自动检测",
    "zh": "中文（普通话）",
    "yue": "粤语",
    "en": "英语",
    "ja": "日语",
    "ko": "韩语",
    "fr": "法语",
    "de": "德语",
    "es": "西班牙语",
    "ru": "俄语",
    "pt": "葡萄牙语",
    "it": "意大利语",
    "ar": "阿拉伯语",
    "th": "泰语",
    "vi": "越南语",
    "id": "印尼语",
}

MODELS = {
    "large-v3": {
        "label": "large-v3 · 多语言质量最佳",
        "vram": 5000, "folder": "faster-whisper-large-v3",
        "langs": ["zh", "yue", "en", "ja", "ko", "de"],
        "desc": "官方多语言最强，中文/粤语/日语/韩语/德语首选；速度较慢。",
    },
    "large-v3-turbo": {
        "label": "large-v3-turbo · 接近 large，速度快 6-8x",
        "vram": 4000, "folder": "faster-whisper-large-v3-turbo",
        "langs": ["zh", "yue", "en", "ja", "ko", "de"],
        "desc": "large-v3 的蒸馏加速版，质量略低但快很多，适合日常批量。",
    },
    "distil-large-v3": {
        "label": "distil-large-v3 · 英语专用",
        "vram": 3500, "folder": "faster-distil-whisper-large-v3",
        "langs": ["en"],
        "desc": "英语专用蒸馏模型，速度约快 6 倍，英语质量接近 large-v3。",
    },
    "medium": {
        "label": "medium · 均衡",
        "vram": 2500, "folder": "faster-whisper-medium",
        "langs": ["zh", "yue", "en", "ja", "ko", "de"],
        "desc": "质量与速度平衡，CPU 也可用。",
    },
    "small": {
        "label": "small · 快速",
        "vram": 1500, "folder": "faster-whisper-small",
        "langs": ["zh", "yue", "en", "ja", "ko", "de"],
        "desc": "速度快、质量一般，适合预览或低配置。",
    },
    "base": {
        "label": "base · 极速",
        "vram": 800, "folder": "faster-whisper-base",
        "langs": ["zh", "yue", "en", "ja", "ko", "de"],
        "desc": "极快，质量较低，仅建议快速草稿。",
    },
    "tiny": {
        "label": "tiny · 最低配置",
        "vram": 500, "folder": "faster-whisper-tiny",
        "langs": ["zh", "yue", "en", "ja", "ko", "de"],
        "desc": "最小最快，质量最低。",
    },
}

PLATFORMS = {
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "instagram": "Instagram Reels",
    "x": "X (Twitter)",
    "facebook": "Facebook",
    "bilibili": "Bilibili",
    "reddit": "Reddit",
}

PLATFORM_VIDEO_SPECS = {
    "reddit": {
        "label": "Reddit", "container": ["mp4", "mov"], "vcodec": ["h264"], "acodec": ["aac"],
        "max_w": 1920, "max_h": 1080, "max_fps": 30, "max_duration": 900,
        "max_size_mb": 1024, "crf": 20, "maxrate": "8M", "bufsize": "16M",
        "min_h": 720, "min_bitrate": 500000,
        "vertical": False,
        "note": "H.264 8-bit 4:2:0 + AAC 48kHz，恒定帧率，faststart；≤15 分钟 / ≤1GB / ≤1080p。VFR、10-bit、4:2:2、非 AAC、MOV 容器、过低分辨率/码率都容易导致 Reddit 处理失败。",
    },
    "youtube": {
        "label": "YouTube", "container": ["mp4", "mov"], "vcodec": ["h264", "hevc"], "acodec": ["aac"],
        "max_w": 3840, "max_h": 2160, "max_fps": 60, "max_duration": 43200,
        "max_size_mb": 128000, "crf": 18, "maxrate": None, "bufsize": None,
        "vertical": False, "note": "H.264 + AAC，1080p/4K，faststart",
    },
    "tiktok": {
        "label": "TikTok", "container": ["mp4", "mov"], "vcodec": ["h264", "hevc"], "acodec": ["aac"],
        "max_w": 1080, "max_h": 1920, "max_fps": 60, "max_duration": 3600,
        "max_size_mb": 4096, "crf": 20, "maxrate": None, "bufsize": None,
        "vertical": True, "note": "竖屏 9:16，1080×1920，H.264 + AAC",
    },
    "instagram": {
        "label": "Instagram Reels", "container": ["mp4", "mov"], "vcodec": ["h264", "hevc"], "acodec": ["aac"],
        "max_w": 1080, "max_h": 1920, "max_fps": 30, "max_duration": 180,
        "max_size_mb": 1024, "crf": 20, "maxrate": None, "bufsize": None,
        "vertical": True, "note": "竖屏 9:16，建议 ≤90 秒，H.264 + AAC",
    },
    "x": {
        "label": "X (Twitter)", "container": ["mp4", "mov"], "vcodec": ["h264"], "acodec": ["aac"],
        "max_w": 1920, "max_h": 1080, "max_fps": 60, "max_duration": 140,
        "max_size_mb": 512, "crf": 20, "maxrate": "6M", "bufsize": "12M",
        "vertical": False, "note": "H.264 + AAC，≤2:20 / ≤512MB（免费账号）",
    },
    "facebook": {
        "label": "Facebook", "container": ["mp4", "mov"], "vcodec": ["h264", "hevc"], "acodec": ["aac"],
        "max_w": 1920, "max_h": 1080, "max_fps": 60, "max_duration": 14400,
        "max_size_mb": 10240, "crf": 20, "maxrate": None, "bufsize": None,
        "vertical": False, "note": "H.264 + AAC，1080p",
    },
    "bilibili": {
        "label": "Bilibili", "container": ["mp4", "flv"], "vcodec": ["h264", "hevc"], "acodec": ["aac"],
        "max_w": 1920, "max_h": 1080, "max_fps": 60, "max_duration": 43200,
        "max_size_mb": 8192, "crf": 20, "maxrate": None, "bufsize": None,
        "vertical": False, "note": "H.264 + AAC，1080p",
    },
}

VIDEO_DIR = APP_DIR / "video-work"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_HISTORY = APP_DIR / "video_history.json"
VIDEO_JOBS = {}
VIDEO_LOCK = threading.Lock()

JOBS = {}
JOBS_LOCK = threading.Lock()
_ENV_CACHE = {"ts": 0.0, "data": None}

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".flv", ".wmv", ".webm", ".m4v",
             ".mpg", ".mpeg", ".ts", ".m2ts", ".rmvb", ".3gp"}


# ---------------------------------------------------------------- 基础工具
def now_str():
    return time.strftime("%Y%m%d_%H%M%S")


def safe_name(name: str) -> str:
    name = os.path.basename(str(name).replace("\\", "/"))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()
    if not name:
        name = "video.mp4"
    stem, ext = os.path.splitext(name)
    if len(name) > 150:
        name = stem[:140] + ext
    return name


def add_log(job, line):
    line = ANSI_RE.sub("", str(line)).rstrip()
    if not line:
        return
    job["logs"].append(line)
    if len(job["logs"]) > MAX_LOG:
        job["logs"] = job["logs"][-MAX_LOG:]


def set_state(job, state, message=None, progress=None):
    changed = False
    with JOBS_LOCK:
        changed = job.get("state") != state
        job["state"] = state
        if message is not None:
            job["message"] = message
        if progress is not None:
            job["progress"] = progress
    if changed:
        persist_history()


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_llm():
    cfg = load_config()
    provider = (cfg.get("llmProvider") or "deepseek").lower()
    if provider == "qwen":
        key = (cfg.get("dashscopeKey") or os.environ.get("DASHSCOPE_API_KEY") or "").strip()
        return "qwen", key
    key = (cfg.get("deepseekKey") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    return "deepseek", key


def get_deepseek_key():
    """当前启用的 AI 服务 Key（兼容旧调用）。"""
    return get_llm()[1]


def mask_key(k):
    if not k:
        return ""
    if len(k) <= 10:
        return k[:2] + "***"
    return k[:6] + "..." + k[-4:]


def read_text(path: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- 环境检查
def _total_ram_gb():
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(st)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return round(st.ullTotalPhys / (1024 ** 3), 1)
    except Exception:
        return 0.0


def _nvidia_gpus():
    gpus = []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=12,
            creationflags=CREATE_NO_WINDOW,
        ).stdout
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                try:
                    gpus.append({"name": parts[0], "vram_mb": int(float(parts[1])), "driver": parts[2]})
                except ValueError:
                    pass
    except Exception:
        pass
    return gpus


def find_exe():
    for p in TOOL_DIR.rglob("faster-whisper-xxl.exe"):
        return p
    return None


def installed_models():
    if not MODEL_DIR.exists():
        return []
    return sorted([p.name for p in MODEL_DIR.iterdir() if p.is_dir()])


def sense_available():
    try:
        return (importlib.util.find_spec("funasr") is not None
                and importlib.util.find_spec("torch") is not None)
    except Exception:
        return False


def check_env(force=False):
    if not force and _ENV_CACHE["data"] and time.time() - _ENV_CACHE["ts"] < 20:
        return _ENV_CACHE["data"]

    exe = find_exe()
    sense_ok = sense_available()
    gpus = _nvidia_gpus()
    ram = _total_ram_gb()
    try:
        free_gb = round(shutil.disk_usage(str(APP_DIR)).free / (1024 ** 3), 1)
    except Exception:
        free_gb = 0.0
    ffmpeg = shutil.which("ffmpeg")
    models = installed_models()
    cfg = load_config()
    provider = (cfg.get("llmProvider") or "deepseek").lower()
    deepseek_key = (cfg.get("deepseekKey") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    qwen_key = (cfg.get("dashscopeKey") or os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    key = qwen_key if provider == "qwen" else deepseek_key
    provider_label = "Qwen (DashScope)" if provider == "qwen" else "DeepSeek"

    items = [
        {"key": "python", "label": "Python 运行环境", "status": "ok",
         "detail": sys.version.split()[0]},
        {"key": "tool", "label": "转录引擎 faster-whisper-xxl",
         "status": "ok" if exe else "warn",
         "detail": str(exe) if exe else "未安装，首次生成时会自动下载（约 1～2GB）"},
        {"key": "sensevoice", "label": "SenseVoice 转录引擎",
         "status": "ok" if sense_ok else "warn",
         "detail": "已安装（中文/粤语推荐）" if sense_ok else "未安装，仅 Whisper 可用"},
        {"key": "gpu", "label": "NVIDIA GPU",
         "status": "ok" if gpus else "warn",
         "detail": "；".join(f'{g["name"]} · {g["vram_mb"] / 1024:.1f}GB · 驱动 {g["driver"]}' for g in gpus)
                   if gpus else "未检测到，将使用 CPU（较慢）"},
        {"key": "cuda", "label": "GPU 加速",
         "status": "ok" if gpus else "warn",
         "detail": "可用（cuda / float16）" if gpus else "不可用，回退 CPU / int8"},
        {"key": "ffmpeg", "label": "FFmpeg", "status": "ok" if ffmpeg else "warn",
         "detail": ffmpeg or "未安装（部分格式可能无法读取）"},
        {"key": "cpu", "label": "CPU", "status": "ok",
         "detail": f"{platform.processor() or '未知'} · {os.cpu_count()} 线程"},
        {"key": "ram", "label": "内存", "status": "ok" if ram >= 8 else "warn",
         "detail": f"{ram} GB"},
        {"key": "disk", "label": "磁盘可用空间", "status": "ok" if free_gb >= 15 else "warn",
         "detail": f"D 盘 {free_gb} GB"},
        {"key": "model", "label": "已下载模型",
         "status": "ok" if models else "warn",
         "detail": "、".join(models) if models else "未下载，首次生成时自动准备"},
        {"key": "deepseek", "label": f"AI 服务（{provider_label}）",
         "status": "ok" if key else "warn",
         "detail": ("已配置 " + mask_key(key)) if key else f"未配置 {provider_label} Key（字幕校对 / 翻译 / 文案需要）"},
    ]

    data = {
        "ok": True,
        "items": items,
        "gpu": gpus,
        "ram_gb": ram,
        "cpu": platform.processor() or "",
        "threads": os.cpu_count(),
        "disk_free_gb": free_gb,
        "models": models,
        "toolInstalled": bool(exe),
        "sensevoiceInstalled": sense_ok,
        "llmProvider": provider,
        "deepseekConfigured": bool(deepseek_key),
        "qwenConfigured": bool(qwen_key),
        "python": sys.version.split()[0],
    }
    _ENV_CACHE["ts"] = time.time()
    _ENV_CACHE["data"] = data
    return data


def recommend_model(pref="balanced", source="auto"):
    env = check_env()
    gpus = env["gpu"]
    vram = max([g["vram_mb"] for g in gpus], default=0)

    if vram >= 6000:
        device, compute = "cuda", "float16"
    elif vram >= 3500:
        device, compute = "cuda", "int8_float16"
    else:
        device, compute = "cpu", "int8"

    multi_langs = ("zh", "yue", "ja", "ko", "de")

    if source == "en":
        if pref == "quality":
            model = "large-v3"
        elif pref == "speed":
            model = "small" if vram < 2000 else "medium"
        else:
            model = "distil-large-v3"
    elif source in multi_langs:
        if pref == "quality":
            model = "large-v3"
        elif pref == "speed":
            model = "small" if vram < 1500 else "medium"
        else:
            model = "large-v3-turbo"
    else:
        if pref == "quality":
            model = "large-v3"
        elif pref == "speed":
            model = "small" if vram < 1500 else "medium"
        else:
            model = "large-v3-turbo"

    if device == "cpu" and pref != "quality" and model in ("large-v3", "large-v3-turbo"):
        model = "medium"

    need = MODELS[model]["vram"]
    if vram and vram < need:
        for m in ("large-v3-turbo", "distil-large-v3", "medium", "small", "base", "tiny"):
            if MODELS[m]["vram"] <= vram:
                model = m
                break

    # 模型还没下载完时，按偏好回退到已安装的模型
    target_model = model
    if not (MODEL_DIR / MODELS[model]["folder"] / "model.bin").exists():
        fallback = {
            "quality": ["large-v3", "large-v3-turbo", "distil-large-v3", "medium", "small", "base", "tiny"],
            "balanced": ["large-v3-turbo", "large-v3", "distil-large-v3", "medium", "small", "base", "tiny"],
            "speed": ["small", "base", "tiny", "medium", "large-v3-turbo", "large-v3", "distil-large-v3"],
        }.get(pref, ["large-v3", "medium", "small", "base", "tiny"])
        if source == "en":
            fallback = ["distil-large-v3"] + [m for m in fallback if m != "distil-large-v3"]
        for m in fallback:
            if (MODEL_DIR / MODELS[m]["folder"] / "model.bin").exists():
                model = m
                break

    lang_names = {"zh": "中文（普通话）", "yue": "粤语", "en": "英语",
                  "ja": "日语", "ko": "韩语", "de": "德语"}
    if gpus:
        reason = (f'检测到 {gpus[0]["name"]}（{gpus[0]["vram_mb"] / 1024:.1f}GB 显存），'
                  f'可启用 CUDA 加速。')
    else:
        reason = "未检测到可用 NVIDIA GPU，将使用 CPU（建议 medium/small 以控制耗时）。"

    if source == "en":
        if model == "distil-large-v3":
            reason += " 英语：选择 distil-large-v3（英语专用，速度约快 6 倍，质量接近 large-v3）。"
        elif model == "large-v3":
            reason += " 英语：质量优先选 large-v3（官方最强多语言模型）。"
        else:
            reason += " 英语：速度优先，选择较小模型。"
    elif source in multi_langs:
        if model == "large-v3":
            reason += f" {lang_names[source]}：选择 large-v3（官方多语言质量最佳）。"
        elif model == "large-v3-turbo":
            reason += f" {lang_names[source]}：选择 large-v3-turbo（接近 large 质量，速度快 6-8 倍）。"
        else:
            reason += f" {lang_names[source]}：速度优先，选择较小模型。"
        if source == "yue":
            reason += " Whisper 对粤语支持有限，建议同时填写专有名词；必要时用 large-v3 复核。"
        if source == "ja":
            reason += " 日语建议填写作品名与角色名，可显著减少同音词错误。"
    else:
        reason += " 未指定语言：默认选择多语言模型 large-v3 / turbo。"

    if model != target_model:
        reason += f"（{MODELS[target_model]['label']} 尚未下载完成，暂用 {model}）"

    reason += {"quality": " 已按“质量优先”选择。",
               "speed": " 已按“速度优先”选择。",
               "balanced": " 已按“均衡”选择。"}.get(pref, "")

    engine = "sensevoice" if source in ("zh", "yue") else "whisper"
    if engine == "sensevoice" and not sense_available():
        engine = "whisper"
    if source in ("zh", "yue"):
        reason += " 转录引擎：SenseVoice（中文/粤语专用，VAD 分段时间轴）。"
    else:
        reason += " 转录引擎：Whisper（多语言）。"

    return {
        "model": model,
        "device": device,
        "computeType": compute,
        "engine": engine,
        "reason": reason,
        "preference": pref,
        "options": [{
            "value": k, "label": v["label"], "desc": v.get("desc", ""),
            "installed": (MODEL_DIR / v["folder"] / "model.bin").exists(),
        } for k, v in MODELS.items()],
    }


# ---------------------------------------------------------------- 下载 / 工具
def download_sources(url):
    """GitHub 直连在国内很慢，优先走加速镜像。"""
    sources = []
    if "github.com/" in url and "/releases/download/" in url:
        sources.append("https://gh-proxy.com/" + url)
        sources.append("https://ghfast.top/" + url)
    sources.append(url)
    seen, out = set(), []
    for s in sources:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _download_one(url, tmp: Path, job):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        last = 0.0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            t = time.time()
            if t - last > 0.5:
                last = t
                if total:
                    pct = int(got * 100 / total)
                    job["message"] = f"下载 {pct}%（{got // 1048576} / {total // 1048576} MB）"
                    job["progress"] = min(90, pct)
                else:
                    job["message"] = f"下载中… {got // 1048576} MB"
        if total and got < total:
            raise RuntimeError(f"文件不完整 {got}/{total}")


def download_file(url, dest: Path, job):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    last_err = None
    for src in download_sources(url):
        try:
            add_log(job, f"下载源: {src}")
            _download_one(src, tmp, job)
            os.replace(tmp, dest)
            add_log(job, f"下载完成: {dest.name}")
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            add_log(job, f"该下载源失败：{e}，尝试下一个…")
            try:
                tmp.unlink()
            except OSError:
                pass
    raise RuntimeError(f"下载失败：{last_err}")


def ensure_tool(job):
    exe = find_exe()
    if exe:
        return exe
    add_log(job, "首次运行：未找到转录工具，开始下载（约 1～2GB，请耐心等待）…")
    set_state(job, "preparing", "下载转录工具…", 1)
    sevenzip = TOOL_DIR / "7zr.exe"
    archive = TOOL_DIR / "faster-whisper.7z"
    if not sevenzip.exists():
        download_file(SEVENZIP_URL, sevenzip, job)
    if not archive.exists():
        download_file(TOOL_ARCHIVE_URL, archive, job)
    add_log(job, "解压转录工具…")
    set_state(job, "preparing", "解压转录工具…", 2)
    subprocess.run([str(sevenzip), "x", str(archive), f"-o{TOOL_DIR}", "-y"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    exe = find_exe()
    if not exe:
        raise RuntimeError("解压后未找到 faster-whisper-xxl.exe")
    return exe


def seed_model(job, model):
    # faster-whisper-xxl 期望的目录名形如 faster-whisper-large-v3
    repo = "faster-whisper-large-v3" if model == "large-v3" else f"faster-whisper-{model}"
    target = MODEL_DIR / repo
    if (target / "model.bin").exists():
        return
    snapshots = (Path.home() / ".cache" / "huggingface" / "hub"
                 / "models--Systran--faster-whisper-large-v3" / "snapshots")
    if model != "large-v3" or not snapshots.exists():
        return
    for snap in snapshots.iterdir():
        if not (snap / "model.bin").exists():
            continue
        target.mkdir(parents=True, exist_ok=True)
        for f in snap.iterdir():
            if not f.is_file():
                continue
            dest = target / f.name
            if dest.exists():
                continue
            try:
                os.link(f.resolve(), dest)
            except OSError:
                try:
                    shutil.copy2(f.resolve(), dest)
                except OSError:
                    pass
        add_log(job, f"已复用本地模型缓存：{snap.name}")
        return


def first_srt(folder: Path):
    if not folder.exists():
        return None
    files = sorted(folder.glob("*.srt"))
    for f in files:
        if f.stat().st_size > 0:
            return str(f)
    return str(files[0]) if files else None


def probe_duration(path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30,
            creationflags=CREATE_NO_WINDOW,
        ).stdout.strip()
        return float(out) if out else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------- SRT / DeepSeek
def parse_srt(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    blocks = re.split(r"\n\s*\n", text)
    cues = []
    for b in blocks:
        lines = b.split("\n")
        if len(lines) < 2:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        timecode = lines[1].strip()
        if "-->" not in timecode:
            continue
        content = "\n".join(lines[2:]).strip()
        cues.append({"i": idx, "time": timecode, "text": content})
    return cues


def build_srt(cues):
    return "\n\n".join(f'{c["i"]}\n{c["time"]}\n{c["text"]}' for c in cues) + "\n"


def parse_json_loose(s):
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def llm_chat(api_key, system, user, json_mode=True, timeout=180):
    provider = (load_config().get("llmProvider") or "deepseek").lower()
    if provider == "qwen":
        url = QWEN_URL
        model = "qwen-plus"
    else:
        url = DEEPSEEK_URL
        model = "deepseek-chat"

    last_err = None
    for attempt in range(4):
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "stream": False,
        }
        # DeepSeek 支持严格 JSON；Qwen 用提示词 + 容错解析
        if json_mode and provider != "qwen":
            payload["response_format"] = {"type": "json_object"}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                obj = json.loads(r.read().decode("utf-8"))
            return obj["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            last_err = f"AI API 错误 {e.code}: {body[:300]}"
            if e.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(last_err)
        except Exception as e:  # noqa: BLE001
            last_err = f"AI 请求失败: {e}"
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(last_err)
    raise RuntimeError(last_err or "AI 请求失败")


def llm_translate(job, cues, target_label, api_key):
    batch_size = 40
    total = len(cues)
    result = []
    for start in range(0, total, batch_size):
        batch = cues[start:start + batch_size]
        payload = [{"i": c["i"], "t": c["text"]} for c in batch]
        system = "你是专业字幕翻译，只输出 JSON，不要输出解释。"
        user = (
            f"把下面的字幕文本翻译成{target_label}。要求：口语化、简洁、符合字幕阅读习惯；"
            f"严格保持每条 i 与原文一一对应；不要合并或拆分条目。\n"
            f'返回 JSON：{{"items":[{{"i":序号,"t":"译文"}}]}}\n'
            f"输入：{json.dumps(payload, ensure_ascii=False)}"
        )
        raw = llm_chat(api_key, system, user)
        obj = parse_json_loose(raw)
        mp = {int(x["i"]): str(x.get("t", "")).strip() for x in obj.get("items", [])}
        for c in batch:
            nc = dict(c)
            nc["text"] = mp.get(c["i"], c["text"])
            result.append(nc)
        done = min(start + len(batch), total)
        job["progress"] = min(95, 55 + int(40 * done / total))
        job["message"] = f"AI 翻译中… {done}/{total} 条"
        time.sleep(0.2)
    return result


def llm_review(job, cues, lang_label, api_key):
    batch_size = 40
    total = len(cues)
    changes, notes, out = [], [], []
    for start in range(0, total, batch_size):
        batch = cues[start:start + batch_size]
        payload = [{"i": c["i"], "t": c["text"]} for c in batch]
        system = "你是资深字幕校对，只输出 JSON，不要输出解释。"
        user = (
            f"校对下面的{lang_label}字幕：修正语音识别错误、错别字、专有名词、语句不通顺、"
            f"明显误译以及标点断句问题。不要改变条目编号，不要合并或拆分条目，只返回需要修改的条目。\n"
            f'返回 JSON：{{"items":[{{"i":序号,"t":"修正后文本"}}],"notes":"总体说明"}}\n'
            f"输入：{json.dumps(payload, ensure_ascii=False)}"
        )
        raw = llm_chat(api_key, system, user)
        obj = parse_json_loose(raw)
        mp = {int(x["i"]): str(x.get("t", "")).strip() for x in obj.get("items", [])}
        if obj.get("notes"):
            notes.append(str(obj["notes"]))
        for c in batch:
            new = mp.get(c["i"])
            if new and new != c["text"]:
                changes.append({"i": c["i"], "before": c["text"], "after": new})
                nc = dict(c)
                nc["text"] = new
                out.append(nc)
            else:
                out.append(dict(c))
        done = min(start + len(batch), total)
        job["reviewProgress"] = int(100 * done / total)
        job["reviewMessage"] = f"AI 校对中… {done}/{total} 条"
        time.sleep(0.2)
    return out, {"changed": len(changes), "total": total,
                 "samples": changes[:40], "notes": "\n".join(notes)}


# ---------------------------------------------------------------- 转录执行
def run_process(job, cmd, base, span):
    env = os.environ.copy()
    env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    env.setdefault("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    add_log(job, "$ " + " ".join(f'"{c}"' if " " in c else c for c in cmd[1:]))
    proc = subprocess.Popen(
        cmd, cwd=str(Path(cmd[0]).parent),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
    )
    job["proc"] = proc
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    buf = ""
    while True:
        data = proc.stdout.read(4096)
        if not data:
            break
        buf += decoder.decode(data)
        while True:
            m = re.search(r"[\r\n]", buf)
            if not m:
                break
            line = buf[:m.start()]
            buf = buf[m.end():]
            if line.strip():
                handle_line(job, line.strip(), base, span)
    if buf.strip():
        handle_line(job, buf.strip(), base, span)
    rc = proc.wait()
    job["proc"] = None
    if rc != 0:
        raise RuntimeError(f"转录程序退出码 {rc}（详见日志）")


def handle_line(job, line, base, span):
    add_log(job, line)
    mp = re.search(r"PROGRESS\s+(\d{1,3})", line)
    if mp:
        pct = max(0, min(100, int(mp.group(1))))
        job["progress"] = min(99, base + int(span * pct / 100))
    m = re.search(r"(\d{1,3})%", line)
    if m:
        pct = max(0, min(100, int(m.group(1))))
        job["progress"] = min(99, base + int(span * pct / 100))
    # 从字幕时间轴推算进度： [MM:SS.mmm --> MM:SS.mmm] 或 [HH:MM:SS,mmm --> ...]
    mt = re.search(r"-->\s*(?:(\d+):)?(\d+):(\d+)[.,](\d+)", line)
    if mt:
        h = int(mt.group(1)) if mt.group(1) else 0
        mm, ss, ms = int(mt.group(2)), int(mt.group(3)), int(mt.group(4))
        secs = h * 3600 + mm * 60 + ss + ms / 1000
        dur = job.get("duration") or 0
        if dur > 0:
            job["progress"] = min(99, base + int(span * min(1.0, secs / dur)))
    if len(line) < 200:
        job["message"] = line
    d = re.search(r"detected language[:\s]+([A-Za-z][A-Za-z ()_\-]+)", line, re.I)
    if d:
        job["detectedLang"] = d.group(1).strip()


def transcribe_cmd(exe, video, source, model, device, compute, task, out_dir,
                   prompt=None, hotwords=None):
    cmd = [str(exe), str(video)]
    if source and source != "auto":
        cmd += ["--language", source]
    cmd += [
        "--model", model,
        "--model_dir", str(MODEL_DIR),
        "--device", device,
        "--compute_type", compute,
        "--output_format", "srt",
        "--beam_size", "8",
        "--condition_on_previous_text", "False",
        "--vad_filter", "False",
        "--standard",
        "--task", task,
        "--output_dir", str(out_dir),
    ]
    if prompt:
        cmd += ["--initial_prompt", prompt]
    if hotwords:
        cmd += ["--hotwords", hotwords]
    return cmd


def run_transcribe(job, params, task, out_dir, base, span):
    device = params["device"]
    compute = params["computeType"]
    run_video = job.get("run_path") or job["path"]
    cmd = transcribe_cmd(job["exe"], run_video, params["sourceLang"],
                         params["model"], device, compute, task, out_dir,
                         params.get("initialPrompt"), params.get("hotwords"))
    try:
        run_process(job, cmd, base, span)
    except RuntimeError as e:
        # 有些情况下工具在退出时崩溃，但字幕其实已经写出来了
        if first_srt(Path(out_dir)):
            add_log(job, f"⚠ 转录程序退出异常（{e}），但字幕文件已生成，直接使用。")
            return
        if device != "cpu":
            add_log(job, f"⚠ GPU 加速失败（{e}），自动回退 CPU / int8 重试…")
            set_state(job, job["state"], "GPU 失败，回退 CPU 重试…")
            for f in Path(out_dir).glob("*.srt"):
                try:
                    f.unlink()
                except OSError:
                    pass
            cmd = transcribe_cmd(job["exe"], run_video, params["sourceLang"],
                                 params["model"], "cpu", "int8", task, out_dir,
                                 params.get("initialPrompt"), params.get("hotwords"))
            run_process(job, cmd, base, span)
        else:
            raise


def run_job(job):
    try:
        params = job["params"]
        src = params["sourceLang"]
        tgt = params["targetLang"]
        engine = params.get("engine", "auto")
        if engine == "auto":
            engine = "sensevoice" if src in ("zh", "yue") else "whisper"
        if engine == "sensevoice" and not sense_available():
            add_log(job, "SenseVoice 未安装，回退 Whisper")
            engine = "whisper"
        if engine == "sensevoice" and src not in ("auto", "zh", "yue", "en", "ja", "ko"):
            add_log(job, f"SenseVoice 不支持语言 {src}，回退 Whisper")
            engine = "whisper"
        job["engine"] = engine

        if engine == "whisper":
            job["exe"] = ensure_tool(job)
            seed_model(job, params["model"])
            model_folder = MODELS[params["model"]]["folder"]
            if not (MODEL_DIR / model_folder / "model.bin").exists():
                raise RuntimeError(f"模型 {params['model']} 尚未下载完成，请稍后重试")

        video = Path(job["path"])
        # 用纯 ASCII 文件名跑工具：避免全角/日文/空格文件名导致工具写 SRT 时崩溃
        run_video = video
        try:
            if any(ord(ch) > 127 for ch in video.name) or " " in video.name or "[" in video.name:
                run_video = job["dir"] / ("input" + (video.suffix or ".mp4"))
                if run_video.exists():
                    run_video.unlink()
                try:
                    os.link(video, run_video)
                except OSError:
                    shutil.copy2(video, run_video)
        except Exception:
            run_video = video
        job["run_path"] = str(run_video)

        out = job["dir"] / ("Subtitles_" + now_str())
        raw_dir = out / "Source"
        sub_dir = out / "Subtitles"
        raw_dir.mkdir(parents=True, exist_ok=True)
        sub_dir.mkdir(parents=True, exist_ok=True)

        base = os.path.splitext(job["name"])[0]

        if engine == "sensevoice":
            set_state(job, "transcribing", "SenseVoice 转录中…", 2)
            raw_srt = raw_dir / f"{base}.srt"
            cmd = [sys.executable, "-u", str(SENSEVOICE_WORKER), str(run_video),
                   "--output", str(raw_srt), "--language", src, "--device", "cpu"]
            run_process(job, cmd, 2, 60)
            if not raw_srt.exists():
                raise RuntimeError("SenseVoice 未生成字幕文件")
            job["raw"] = str(raw_srt)
            if tgt in ("same", src):
                job["sub"] = str(raw_srt)
            else:
                key = get_deepseek_key()
                if not key:
                    raise RuntimeError("翻译到该语言需要 AI API Key，请在设置中填写后重试")
                provider, _ = get_llm()
                set_state(job, "translating", f"AI 翻译中（{provider}）…", 65)
                cues = parse_srt(read_text(raw_srt))
                translated = llm_translate(job, cues, LANGUAGES.get(tgt, tgt), key)
                sub_path = sub_dir / f"{base}_{tgt}.srt"
                sub_path.write_text(build_srt(translated), encoding="utf-8")
                job["sub"] = str(sub_path)
        elif tgt == "same" or tgt == src:
            set_state(job, "transcribing", "正在生成字幕…", 2)
            run_transcribe(job, params, "transcribe", raw_dir, 2, 96)
            p = first_srt(raw_dir)
            if not p:
                raise RuntimeError("未生成字幕文件")
            job["raw"] = p
            job["sub"] = p
        elif tgt == "en":
            set_state(job, "transcribing", "正在转写并翻译为英语…", 2)
            run_transcribe(job, params, "translate", sub_dir, 2, 96)
            p = first_srt(sub_dir)
            if not p:
                raise RuntimeError("未生成英文字幕")
            job["sub"] = p
        else:
            set_state(job, "transcribing", "正在转写原语言字幕…", 2)
            run_transcribe(job, params, "transcribe", raw_dir, 2, 42)
            raw = first_srt(raw_dir)
            if not raw:
                raise RuntimeError("未生成原语言字幕")
            job["raw"] = raw

            key = get_deepseek_key()
            if not key:
                raise RuntimeError("翻译到该语言需要 AI API Key，请在设置中填写后重试")
            provider, _ = get_llm()
            set_state(job, "translating", f"AI 翻译中（{provider}）…", 50)
            cues = parse_srt(read_text(Path(raw)))
            translated = llm_translate(job, cues, LANGUAGES.get(tgt, tgt), key)
            sub_path = sub_dir / f"{base}_{tgt}.srt"
            sub_path.write_text(build_srt(translated), encoding="utf-8")
            job["sub"] = str(sub_path)

        set_state(job, "done", "完成", 100)
        if not params.get("keepVideo"):
            for p in {video, Path(job.get("run_path") or video)}:
                try:
                    p.unlink()
                except OSError:
                    pass
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        add_log(job, "ERROR: " + msg)
        job["error"] = msg
        set_state(job, "error", "失败：" + msg)
    finally:
        job["proc"] = None
        rp = job.get("run_path")
        if rp and job.get("path") and Path(rp) != Path(job["path"]):
            try:
                Path(rp).unlink()
            except OSError:
                pass


def run_review(job, api_key):
    try:
        job["reviewState"] = "running"
        job["reviewProgress"] = 0
        job["reviewMessage"] = "准备校对…"
        path = job.get("sub") or job.get("raw")
        if not path:
            raise RuntimeError("没有可校对的字幕")
        cues = parse_srt(read_text(Path(path)))
        if not cues:
            raise RuntimeError("字幕解析失败")

        tgt = job["params"]["targetLang"]
        if tgt == "same":
            label = LANGUAGES.get(job["params"]["sourceLang"], "原语言")
        else:
            label = LANGUAGES.get(tgt, tgt)

        reviewed, report = llm_review(job, cues, label, api_key)
        base = os.path.splitext(job["name"])[0]
        out = job["dir"] / "Subtitles_reviewed"
        out.mkdir(parents=True, exist_ok=True)
        p = out / f"{base}_reviewed.srt"
        p.write_text(build_srt(reviewed), encoding="utf-8")
        job["reviewed"] = str(p)
        job["reviewReport"] = report
        job["reviewState"] = "done"
        job["reviewProgress"] = 100
        job["reviewMessage"] = f'校对完成，修改 {report["changed"]} 条'
    except Exception as e:  # noqa: BLE001
        job["reviewState"] = "error"
        job["reviewError"] = str(e)
        job["reviewMessage"] = "校对失败：" + str(e)
        add_log(job, "REVIEW ERROR: " + str(e))


def start_job(job):
    with JOBS_LOCK:
        if job["state"] in ("preparing", "transcribing", "translating"):
            return False
        job["started"] = job["started"] or time.time()
        job["state"] = "preparing"
        job["message"] = "准备中…"
        job["error"] = None
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return True


def cancel_job(job):
    proc = job.get("proc")
    if proc and proc.poll() is None:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            proc.terminate()
    job["error"] = "已取消"
    set_state(job, "error", "已取消")


# ---------------------------------------------------------------- Job
def new_job(name):
    jid = now_str() + "_" + os.urandom(3).hex()
    d = WORK_DIR / jid
    d.mkdir(parents=True, exist_ok=True)
    job = {
        "id": jid, "name": name, "path": None, "size": 0,
        "state": "uploading", "message": "正在上传…", "progress": 0,
        "logs": [], "error": None, "raw": None, "sub": None, "reviewed": None,
        "reviewState": "idle", "reviewProgress": 0, "reviewMessage": "",
        "reviewReport": None, "reviewError": None, "detectedLang": None,
        "publish": None, "publishState": "idle", "publishMessage": "", "publishError": None,
        "publishProgress": 0, "publishInstruction": "",
        "aiEditState": "idle", "aiEditProgress": 0, "aiEditMessage": "",
        "aiEditPlan": None, "aiEditOutput": None, "aiEditError": None,
        "dir": d, "created": time.time(), "started": None, "proc": None,
        "params": None, "exe": None, "duration": 0.0,
    }
    with JOBS_LOCK:
        JOBS[jid] = job
    persist_history()
    return job


def job_public(job):
    def exists(p):
        return bool(p and Path(p).exists())

    return {
        "id": job["id"], "name": job["name"], "size": job["size"],
        "state": job["state"], "message": job["message"], "progress": job["progress"],
        "logs": job["logs"][-100:], "error": job["error"],
        "subReady": exists(job.get("sub")), "rawReady": exists(job.get("raw")),
        "reviewedReady": exists(job.get("reviewed")),
        "reviewState": job.get("reviewState", "idle"),
        "reviewProgress": job.get("reviewProgress", 0),
        "reviewMessage": job.get("reviewMessage", ""),
        "reviewReport": job.get("reviewReport"),
        "reviewError": job.get("reviewError"),
        "detectedLang": job.get("detectedLang"),
        "engine": job.get("engine"),
        "params": job.get("params"),
        "created": job.get("created"),
        "path": job.get("path"),
        "publish": job.get("publish"),
        "publishState": job.get("publishState", "idle"),
        "publishMessage": job.get("publishMessage", ""),
        "publishError": job.get("publishError"),
        "publishProgress": job.get("publishProgress", 0),
        "publishInstruction": job.get("publishInstruction", ""),
        "aiEditState": job.get("aiEditState", "idle"),
        "aiEditProgress": job.get("aiEditProgress", 0),
        "aiEditMessage": job.get("aiEditMessage", ""),
        "aiEditPlan": job.get("aiEditPlan"),
        "aiEditReady": bool(job.get("aiEditOutput") and Path(job["aiEditOutput"]).exists()),
        "aiEditError": job.get("aiEditError"),
        "elapsed": int(time.time() - job["started"]) if job.get("started") else 0,
    }


# ---------------------------------------------------------------- 历史 / 发布文案
def _job_to_record(job):
    return {
        "id": job["id"], "name": job["name"], "size": job.get("size", 0),
        "state": job["state"], "message": job.get("message", ""),
        "progress": job.get("progress", 0), "created": job.get("created"),
        "started": job.get("started"), "path": job.get("path"),
        "params": job.get("params"), "detectedLang": job.get("detectedLang"),
        "raw": job.get("raw"), "sub": job.get("sub"), "reviewed": job.get("reviewed"),
        "reviewState": job.get("reviewState", "idle"),
        "reviewProgress": job.get("reviewProgress", 0),
        "reviewMessage": job.get("reviewMessage", ""),
        "reviewReport": job.get("reviewReport"), "reviewError": job.get("reviewError"),
        "publish": job.get("publish"), "publishState": job.get("publishState", "idle"),
        "publishMessage": job.get("publishMessage", ""), "publishError": job.get("publishError"),
        "publishProgress": job.get("publishProgress", 0),
        "publishInstruction": job.get("publishInstruction", ""),
        "aiEditState": job.get("aiEditState", "idle"),
        "aiEditProgress": job.get("aiEditProgress", 0),
        "aiEditMessage": job.get("aiEditMessage", ""),
        "aiEditPlan": job.get("aiEditPlan"),
        "aiEditOutput": job.get("aiEditOutput"),
        "aiEditError": job.get("aiEditError"),
        "dir": str(job["dir"]),
    }


def persist_history():
    with JOBS_LOCK:
        records = [_job_to_record(j) for j in sorted(
            JOBS.values(), key=lambda x: x.get("created") or 0, reverse=True)]
    tmp = HISTORY_PATH.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, HISTORY_PATH)
    except OSError:
        pass


def _pick_outputs(folder: Path):
    srts = [s for s in folder.rglob("*.srt") if s.is_file()]
    if not srts:
        return None, None, None
    reviewed = [s for s in srts if "review" in str(s).lower()]
    others = [s for s in srts if s not in reviewed]
    subs = [s for s in others if "Subtitles" in s.parts]
    raws = [s for s in others if "Source" in s.parts]
    sub = subs[-1] if subs else (others[-1] if others else None)
    raw = raws[-1] if raws else None
    rev = reviewed[-1] if reviewed else None
    return (str(raw) if raw else None, str(sub) if sub else None, str(rev) if rev else None)


def load_history():
    records = []
    if HISTORY_PATH.exists():
        try:
            records = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                records = []
        except Exception:
            records = []
    known = {r.get("id") for r in records if r.get("id")}

    # 扫描 work 目录，找回没有被记录的任务（重启后文件不会“消失”）
    if WORK_DIR.exists():
        for d in WORK_DIR.iterdir():
            if not d.is_dir() or d.name in known:
                continue
            vids = [f for f in d.iterdir()
                    if f.is_file() and f.suffix.lower() in VIDEO_EXT]
            video = max(vids, key=lambda f: f.stat().st_size) if vids else None
            raw, sub, rev = _pick_outputs(d)
            name = video.name if video else (Path(sub).name if sub else d.name)
            records.append({
                "id": d.name, "name": name,
                "size": video.stat().st_size if video else 0,
                "state": "done" if sub else "interrupted",
                "message": "已从磁盘恢复" if sub else "服务重启，任务中断（可继续生成）",
                "progress": 100 if sub else 0,
                "created": d.stat().st_mtime,
                "started": None, "path": str(video) if video else None,
                "params": None, "detectedLang": None,
                "raw": raw, "sub": sub, "reviewed": rev,
                "reviewState": "idle", "reviewProgress": 0, "reviewMessage": "",
                "reviewReport": None, "reviewError": None,
                "publish": None, "publishState": "idle",
                "publishMessage": "", "publishError": None,
                "dir": str(d),
            })

    for r in records:
        jid = r.get("id")
        if not jid or jid in JOBS:
            continue
        state = r.get("state", "done")
        if state in ("preparing", "transcribing", "translating", "uploading"):
            state = "interrupted"

        publish_state = r.get("publishState", "idle")
        publish_msg = r.get("publishMessage", "")
        publish_err = r.get("publishError")
        if publish_state == "running":
            publish_state = "error"
            publish_msg = "服务重启，文案生成中断，可重新生成"
            publish_err = "interrupted"

        review_state = r.get("reviewState", "idle")
        review_msg = r.get("reviewMessage", "")
        review_err = r.get("reviewError")
        if review_state == "running":
            review_state = "error"
            review_msg = "服务重启，校对中断，可重新校对"
            review_err = "interrupted"

        JOBS[jid] = {
            "id": jid, "name": r.get("name", "video"), "path": r.get("path"),
            "size": r.get("size", 0), "state": state,
            "message": r.get("message", ""), "progress": r.get("progress", 0),
            "logs": [], "error": r.get("error"),
            "raw": r.get("raw"), "sub": r.get("sub"), "reviewed": r.get("reviewed"),
            "reviewState": review_state,
            "reviewProgress": r.get("reviewProgress", 0),
            "reviewMessage": review_msg,
            "reviewReport": r.get("reviewReport"), "reviewError": review_err,
            "publish": r.get("publish"), "publishState": publish_state,
            "publishMessage": publish_msg, "publishError": publish_err,
            "publishProgress": r.get("publishProgress", 0),
            "publishInstruction": r.get("publishInstruction", ""),
            "aiEditState": r.get("aiEditState", "idle"),
            "aiEditProgress": r.get("aiEditProgress", 0),
            "aiEditMessage": r.get("aiEditMessage", ""),
            "aiEditPlan": r.get("aiEditPlan"),
            "aiEditOutput": r.get("aiEditOutput"),
            "aiEditError": r.get("aiEditError"),
            "detectedLang": r.get("detectedLang"),
            "dir": Path(r["dir"]) if r.get("dir") else (WORK_DIR / jid),
            "created": r.get("created") or time.time(), "started": r.get("started"),
            "proc": None, "params": r.get("params"), "exe": None,
            "duration": probe_duration(r["path"]) if r.get("path") and Path(r["path"]).exists() else 0.0,
        }

    persist_history()


PUBLISH_LIMITS = {
    "youtube": "标题 ≤100 字符；简介 2-4 句 + 5-10 个 hashtag；不要编造时间点",
    "tiktok": "caption ≤150 字符，带 5 个 hashtag，口语化、有钩子",
    "instagram": "caption ≤300 字符，带 8 个 hashtag",
    "x": "≤280 字符（含 hashtag），一句话抓眼球",
    "facebook": "标题 ≤100 字符；简介 2-3 句 + hashtag",
    "bilibili": "标题 ≤80 字符；简介 2-3 句 + hashtag",
    "reddit": "标题 ≤300 字符；正文 2-4 句 + 3-5 个相关 subreddit/hashtag；像真实 Reddit 帖子，避免营销腔",
}


def _publish_platform(api_key, platform, transcript):
    label = PLATFORMS.get(platform, platform)
    system = "你是资深海外视频平台运营，只输出 JSON，不要输出解释。"
    user = (
        f"根据下面的字幕内容，为 {label} 生成一条英文标题和简介。\n"
        f"要求：{PUBLISH_LIMITS.get(platform, '')}；抓眼球、有悬念或情绪，不要夸大或编造事实。\n"
        '返回严格 JSON：{"title":"英文标题","description":"英文简介/caption"}\n'
        f"字幕内容：\n{transcript}"
    )
    raw = llm_chat(api_key, system, user, timeout=180)
    obj = parse_json_loose(raw)
    if isinstance(obj, dict) and platform in obj and isinstance(obj[platform], dict):
        obj = obj[platform]
    title = str(obj.get("title", "")).strip() if isinstance(obj, dict) else ""
    desc = str(obj.get("description", "")).strip() if isinstance(obj, dict) else ""
    if not desc and isinstance(obj, dict):
        desc = str(obj.get("caption", "")).strip()
    return {"title": title, "description": desc}


def run_publish(job, api_key, platforms):
    try:
        job["publishState"] = "running"
        job["publishProgress"] = 0
        job["publishMessage"] = "准备生成…"
        job["publishError"] = None
        job["publish"] = {}
        persist_history()

        path = job.get("sub") or job.get("raw")
        if not path:
            raise RuntimeError("没有可用的字幕")
        cues = parse_srt(read_text(Path(path)))
        transcript = "\n".join(c["text"] for c in cues)
        if not transcript.strip():
            raise RuntimeError("字幕内容为空")
        transcript = transcript[:30000]

        total = len(platforms)
        result = {}
        for idx, plat in enumerate(platforms, 1):
            job["publishMessage"] = f"正在生成 {PLATFORMS.get(plat, plat)}（{idx}/{total}）…"
            job["publishProgress"] = int((idx - 1) * 100 / total)
            persist_history()
            result[plat] = _publish_platform(api_key, plat, transcript)
            job["publish"] = dict(result)
            job["publishProgress"] = int(idx * 100 / total)
            persist_history()
            add_log(job, f"文案已生成: {PLATFORMS.get(plat, plat)}")

        job["publish"] = result
        job["publishProgress"] = 100
        job["publishState"] = "done"
        job["publishMessage"] = f"已生成 {len(result)} 个平台文案"
        persist_history()
    except Exception as e:  # noqa: BLE001
        job["publishState"] = "error"
        job["publishError"] = str(e)
        job["publishMessage"] = "生成失败：" + str(e)
        add_log(job, "PUBLISH ERROR: " + str(e))
        persist_history()


def start_publish(job, api_key, platforms):
    if job.get("publishState") == "running":
        return False
    job["publish"] = None
    job["publishState"] = "running"
    job["publishProgress"] = 0
    job["publishMessage"] = "准备生成…"
    persist_history()
    threading.Thread(target=run_publish, args=(job, api_key, platforms), daemon=True).start()
    return True


def _adjust_platform(api_key, platform, current, instruction, transcript):
    label = PLATFORMS.get(platform, platform)
    system = "你是资深海外视频平台运营，只输出 JSON，不要输出解释。"
    cur_title = (current or {}).get("title", "")
    cur_desc = (current or {}).get("description", "")
    user = (
        f"这是 {label} 当前的英文标题和简介：\n"
        f"标题：{cur_title}\n简介：{cur_desc}\n\n"
        f"请根据下面的调整要求重写，保持 {label} 的风格和长度限制：{PUBLISH_LIMITS.get(platform, '')}\n"
        f"调整要求：{instruction}\n\n"
        "可以适当参考字幕内容，但不要编造事实。\n"
        '返回严格 JSON：{"title":"英文标题","description":"英文简介/caption"}\n'
        f"字幕参考：\n{transcript[:8000]}"
    )
    raw = llm_chat(api_key, system, user, timeout=180)
    obj = parse_json_loose(raw)
    if isinstance(obj, dict) and platform in obj and isinstance(obj[platform], dict):
        obj = obj[platform]
    title = str(obj.get("title", "")).strip() if isinstance(obj, dict) else ""
    desc = str(obj.get("description", "")).strip() if isinstance(obj, dict) else ""
    if not desc and isinstance(obj, dict):
        desc = str(obj.get("caption", "")).strip()
    return {"title": title or cur_title, "description": desc or cur_desc}


def run_publish_adjust(job, api_key, platforms, instruction):
    try:
        job["publishState"] = "running"
        job["publishProgress"] = 0
        job["publishMessage"] = "准备调整…"
        job["publishError"] = None
        job["publishInstruction"] = instruction
        persist_history()

        path = job.get("sub") or job.get("raw")
        cues = parse_srt(read_text(Path(path))) if path else []
        transcript = "\n".join(c["text"] for c in cues)[:30000]

        total = len(platforms)
        result = dict(job.get("publish") or {})
        for idx, plat in enumerate(platforms, 1):
            job["publishMessage"] = f"正在调整 {PLATFORMS.get(plat, plat)}（{idx}/{total}）…"
            job["publishProgress"] = int((idx - 1) * 100 / total)
            persist_history()
            result[plat] = _adjust_platform(api_key, plat, result.get(plat), instruction, transcript)
            job["publish"] = dict(result)
            job["publishProgress"] = int(idx * 100 / total)
            persist_history()
            add_log(job, f"文案已调整: {PLATFORMS.get(plat, plat)}")

        job["publish"] = result
        job["publishProgress"] = 100
        job["publishState"] = "done"
        job["publishMessage"] = f"已调整 {len(platforms)} 个平台文案"
        persist_history()
    except Exception as e:  # noqa: BLE001
        job["publishState"] = "error"
        job["publishError"] = str(e)
        job["publishMessage"] = "调整失败：" + str(e)
        add_log(job, "PUBLISH ADJUST ERROR: " + str(e))
        persist_history()


def start_publish_adjust(job, api_key, platforms, instruction):
    if job.get("publishState") == "running":
        return False
    job["publishState"] = "running"
    job["publishProgress"] = 0
    job["publishMessage"] = "准备调整…"
    persist_history()
    threading.Thread(target=run_publish_adjust,
                     args=(job, api_key, platforms, instruction), daemon=True).start()
    return True


# ---------------------------------------------------------------- 视频规格 / 优化
def add_video_log(job, line):
    line = ANSI_RE.sub("", str(line)).rstrip()
    if not line:
        return
    job["logs"].append(line)
    if len(job["logs"]) > 200:
        job["logs"] = job["logs"][-200:]


def _has_faststart(path):
    try:
        with open(path, "rb") as f:
            head = f.read(2 * 1024 * 1024)
        moov = head.find(b"moov")
        mdat = head.find(b"mdat")
        return moov != -1 and (mdat == -1 or moov < mdat)
    except OSError:
        return False


def probe_video(path):
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                         creationflags=CREATE_NO_WINDOW).stdout
    data = json.loads(out or "{}")
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    vs = next((s for s in streams if s.get("codec_type") == "video"), {})
    aus = next((s for s in streams if s.get("codec_type") == "audio"), {})

    def rate(s):
        try:
            n, d = str(s).split("/")
            return float(n) / float(d) if float(d) else 0.0
        except Exception:
            return 0.0

    avg = rate(vs.get("avg_frame_rate", "0/0"))
    r = rate(vs.get("r_frame_rate", "0/0"))
    fps = avg or r
    suffix = Path(path).suffix.lower().lstrip(".")
    fmt_name = fmt.get("format_name") or ""
    if suffix in ("mp4", "m4v"):
        container = "mp4"
    elif suffix == "mov":
        container = "mov"
    elif suffix == "flv":
        container = "flv"
    elif suffix in ("mkv", "webm"):
        container = "matroska"
    else:
        container = fmt_name.split(",")[0]
    return {
        "format": container,
        "ext": suffix,
        "format_long": fmt.get("format_long_name", ""),
        "duration": float(fmt.get("duration") or 0),
        "size": int(fmt.get("size") or 0),
        "bitrate": int(fmt.get("bit_rate") or 0),
        "vcodec": vs.get("codec_name", ""),
        "profile": vs.get("profile", ""),
        "width": int(vs.get("width") or 0),
        "height": int(vs.get("height") or 0),
        "fps": round(fps, 2),
        "r_fps": round(r, 2),
        "vfr": bool(avg and r and abs(avg - r) > 0.5),
        "pix_fmt": vs.get("pix_fmt", ""),
        "has_audio": bool(aus),
        "acodec": aus.get("codec_name", ""),
        "sample_rate": int(aus.get("sample_rate") or 0),
        "channels": int(aus.get("channels") or 0),
        "faststart": _has_faststart(path),
    }


def check_video_spec(info, platform):
    spec = PLATFORM_VIDEO_SPECS[platform]
    issues = []
    if info["format"] not in spec["container"]:
        issues.append(f'容器 {info["format"]} 不符合（需 {", ".join(spec["container"])}）')
    if info["vcodec"] not in spec["vcodec"]:
        issues.append(f'视频编码 {info["vcodec"]} 不符合（需 {", ".join(spec["vcodec"])}）')
    if info["has_audio"] and info["acodec"] not in spec["acodec"]:
        issues.append(f'音频编码 {info["acodec"]} 不符合（需 {", ".join(spec["acodec"])}）')
    if info["width"] > spec["max_w"] or info["height"] > spec["max_h"]:
        issues.append(f'分辨率 {info["width"]}×{info["height"]} 超过 {spec["max_w"]}×{spec["max_h"]}')
    if info["fps"] > spec["max_fps"] + 0.5:
        issues.append(f'帧率 {info["fps"]}fps 超过 {spec["max_fps"]}fps')
    if spec["max_duration"] and info["duration"] > spec["max_duration"]:
        issues.append(f'时长 {int(info["duration"])}s 超过 {spec["max_duration"]}s')
    if info["size"] / 1048576 > spec["max_size_mb"]:
        issues.append(f'文件 {info["size"] / 1048576:.0f}MB 超过 {spec["max_size_mb"]}MB')
    if spec.get("min_h") and info["height"] < spec["min_h"]:
        issues.append(f'分辨率偏低（{info["width"]}×{info["height"]}），建议至少 {spec["min_h"]}p，否则平台转码容易失败')
    if spec.get("min_bitrate") and info["bitrate"] < spec["min_bitrate"]:
        issues.append(f'码率偏低（{info["bitrate"] / 1000:.0f} kbps），建议 ≥{spec["min_bitrate"] // 1000} kbps')
    if platform == "reddit" and info.get("ext") == "mov":
        issues.append("MOV 容器在 Reddit 上更容易处理失败，建议转成 MP4")
    if info["pix_fmt"] not in ("yuv420p", "yuvj420p"):
        issues.append(f'像素格式 {info["pix_fmt"]} 不是 8-bit 4:2:0（Reddit 等平台常因此失败）')
    if info["vfr"]:
        issues.append(f'可变帧率（avg {info["fps"]} vs r {info["r_fps"]}），建议转成恒定帧率')
    if not info["faststart"]:
        issues.append("未启用 faststart（moov 不在文件头），部分平台会处理失败")
    remux_only = (
        info["vcodec"] in spec["vcodec"]
        and (not info["has_audio"] or info["acodec"] in spec["acodec"])
        and info["pix_fmt"] in ("yuv420p", "yuvj420p")
        and info["width"] <= spec["max_w"] and info["height"] <= spec["max_h"]
        and (not spec.get("min_h") or info["height"] >= spec["min_h"])
        and info["fps"] <= spec["max_fps"] + 0.5
        and not info["vfr"]
        and (not spec["max_duration"] or info["duration"] <= spec["max_duration"])
        and info["size"] / 1048576 <= spec["max_size_mb"]
    )
    if not issues:
        fix = "none"
    elif remux_only:
        fix = "remux"
    else:
        fix = "encode"

    return {"platform": platform, "label": spec["label"], "ok": not issues,
            "fix": fix, "issues": issues, "note": spec["note"]}


def build_ffmpeg_cmd(src, dst, platform, info):
    spec = PLATFORM_VIDEO_SPECS[platform]
    w = info["width"] or 1920
    h = info["height"] or 1080
    vf = []
    if spec.get("vertical"):
        vf += ["scale=1080:1920:force_original_aspect_ratio=decrease",
               "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"]
    else:
        target_w, target_h = w, h
        scale = min(spec["max_w"] / w, spec["max_h"] / h, 1.0)
        if scale < 1.0:
            target_w = max(2, int(w * scale) // 2 * 2)
            target_h = max(2, int(h * scale) // 2 * 2)
        min_h = spec.get("min_h", 0)
        if min_h and target_h < min_h:
            target_h = min_h
            target_w = max(2, int(w * (min_h / h)) // 2 * 2)
            if target_w > spec["max_w"]:
                target_w = spec["max_w"] // 2 * 2
        if (target_w, target_h) != (w, h):
            vf.append(f"scale={target_w}:{target_h}")
    vf.append(f"fps={spec['max_fps']}")
    vf.append("format=yuv420p")

    cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", ",".join(vf),
           "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
           "-preset", "medium", "-crf", str(spec["crf"])]
    if spec.get("maxrate"):
        cmd += ["-maxrate", spec["maxrate"], "-bufsize", spec["bufsize"]]
    cmd += ["-fps_mode", "cfr"]
    if info.get("has_audio"):
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
    else:
        cmd += ["-an"]
    cmd += ["-movflags", "+faststart", "-f", "mp4", str(dst)]
    return cmd


def new_video_job(name):
    jid = now_str() + "_" + os.urandom(3).hex()
    d = VIDEO_DIR / jid
    d.mkdir(parents=True, exist_ok=True)
    job = {
        "id": jid, "name": name, "path": None, "size": 0, "dir": d,
        "state": "uploading", "message": "正在上传…", "progress": 0,
        "info": None, "checks": None, "output": None, "created": time.time(),
        "proc": None, "logs": [], "error": None,
        "diagnoseState": "idle", "diagnoseMessage": "", "diagnoseError": None, "diagnose": {},
    }
    with VIDEO_LOCK:
        VIDEO_JOBS[jid] = job
    return job


def build_remux_cmd(src, dst):
    """无损封装：不重编码，只修容器/faststart/负时间戳。"""
    return ["ffmpeg", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?",
            "-c", "copy", "-movflags", "+faststart",
            "-avoid_negative_ts", "make_zero", str(dst)]


def run_ffmpeg_progress(job, cmd, duration, label="编码中", cwd=None):
    add_video_log(job, "$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, bufsize=1, creationflags=CREATE_NO_WINDOW, cwd=cwd)
    job["proc"] = proc
    for line in proc.stderr:
        m = re.search(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", line)
        if m and duration > 0:
            secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            job["progress"] = min(99, int(secs / duration * 100))
            job["message"] = f"{label}… {job['progress']}%"
        if len(line) < 200:
            add_video_log(job, line.strip())
    rc = proc.wait()
    job["proc"] = None
    if rc != 0:
        raise RuntimeError(f"ffmpeg 退出码 {rc}")


def run_optimize(job, platform, mode="auto"):
    try:
        info = job["info"]
        check = (job.get("checks") or {}).get(platform) or {}
        if mode == "auto":
            fix = check.get("fix")
            mode = "remux" if fix in ("none", "remux") else "encode"
        if mode not in ("remux", "encode"):
            mode = "encode"

        job["state"] = "optimizing"
        job["progress"] = 0
        job["message"] = "准备无损封装…" if mode == "remux" else "准备编码…"
        job["output"] = None
        job["error"] = None
        out_dir = job["dir"] / "optimized"
        out_dir.mkdir(exist_ok=True)
        base = os.path.splitext(job["name"])[0]
        out = out_dir / f"{base}_{platform}.mp4"
        dur = info.get("duration") or 0

        if mode == "remux":
            run_ffmpeg_progress(job, build_remux_cmd(job["path"], out), dur, "无损封装中")
        else:
            tmp = out.with_name(out.stem + "_tmp.mp4")
            run_ffmpeg_progress(job, build_ffmpeg_cmd(job["path"], tmp, platform, info), dur, "编码中")
            job["message"] = "编码完成，正在无损封装…"
            run_ffmpeg_progress(job, build_remux_cmd(tmp, out), dur, "无损封装中")
            try:
                tmp.unlink()
            except OSError:
                pass

        job["output"] = str(out)
        job["state"] = "done"
        job["progress"] = 100
        job["message"] = "优化完成（已无损封装）"
        persist_video_history()
    except Exception as e:  # noqa: BLE001
        job["state"] = "error"
        job["message"] = "失败：" + str(e)
        job["error"] = str(e)
        add_video_log(job, "ERROR: " + str(e))
    finally:
        job["proc"] = None


def start_optimize(job, platform, mode="auto"):
    if job.get("state") == "optimizing":
        return False
    job["state"] = "optimizing"
    job["progress"] = 0
    job["message"] = "准备无损封装…" if mode == "remux" else "准备编码…"
    threading.Thread(target=run_optimize, args=(job, platform, mode), daemon=True).start()
    return True


def run_video_diagnose(job, platform, api_key):
    try:
        job["diagnoseState"] = "running"
        job["diagnoseMessage"] = "DeepSeek 正在分析…"
        job["diagnoseError"] = None
        info = job.get("info") or {}
        spec = PLATFORM_VIDEO_SPECS[platform]
        check = (job.get("checks") or {}).get(platform)
        system = "你是视频编码与平台上传规范专家，只输出 JSON，不要输出解释。"
        user = (
            "这是视频的 ffprobe 信息（JSON）：\n"
            f"{json.dumps(info, ensure_ascii=False, indent=1)}\n\n"
            f"目标平台：{spec['label']}\n"
            f"平台规格：{json.dumps(spec, ensure_ascii=False)}\n"
            f"自动检查结果：{json.dumps(check, ensure_ascii=False)}\n\n"
            "请判断是否符合平台要求，并给出具体问题、修复建议和推荐的 ffmpeg 输出参数。\n"
            '返回 JSON：{"ok":true/false,"issues":["..."],"recommendations":["..."],"ffmpeg_args":"..."}\n'
            "ffmpeg_args 不要包含输入文件，只给输出参数。"
        )
        raw = llm_chat(api_key, system, user, timeout=120)
        obj = parse_json_loose(raw)
        job.setdefault("diagnose", {})[platform] = obj
        job["diagnoseState"] = "done"
        job["diagnoseMessage"] = "DeepSeek 检查完成"
        persist_video_history()
    except Exception as e:  # noqa: BLE001
        job["diagnoseState"] = "error"
        job["diagnoseError"] = str(e)
        job["diagnoseMessage"] = "检查失败：" + str(e)


def start_video_diagnose(job, platform, api_key):
    if job.get("diagnoseState") == "running":
        return False
    job["diagnoseState"] = "running"
    job["diagnoseMessage"] = "DeepSeek 正在分析…"
    threading.Thread(target=run_video_diagnose, args=(job, platform, api_key), daemon=True).start()
    return True


def video_job_public(job):
    return {
        "id": job["id"], "name": job["name"], "size": job.get("size", 0),
        "state": job["state"], "message": job.get("message", ""),
        "progress": job.get("progress", 0), "info": job.get("info"),
        "checks": job.get("checks"),
        "outputReady": bool(job.get("output") and Path(job["output"]).exists()),
        "created": job.get("created"), "logs": job.get("logs", [])[-40:],
        "diagnoseState": job.get("diagnoseState", "idle"),
        "diagnoseMessage": job.get("diagnoseMessage", ""),
        "diagnoseError": job.get("diagnoseError"),
        "diagnose": job.get("diagnose", {}),
    }


def persist_video_history():
    with VIDEO_LOCK:
        recs = sorted(VIDEO_JOBS.values(), key=lambda x: x.get("created") or 0, reverse=True)[:30]
        data = [{
            "id": j["id"], "name": j["name"], "size": j.get("size", 0),
            "state": j["state"], "output": j.get("output"), "created": j.get("created"),
            "info": j.get("info"), "checks": j.get("checks"),
            "diagnose": j.get("diagnose", {}),
            "dir": str(j["dir"]), "message": j.get("message", ""),
        } for j in recs]
    tmp = VIDEO_HISTORY.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, VIDEO_HISTORY)
    except OSError:
        pass


def load_video_history():
    if not VIDEO_HISTORY.exists():
        return
    try:
        recs = json.loads(VIDEO_HISTORY.read_text(encoding="utf-8"))
    except Exception:
        return
    for r in recs:
        jid = r.get("id")
        if not jid:
            continue
        state = r.get("state", "done")
        if state in ("uploading", "optimizing"):
            state = "error"
        VIDEO_JOBS[jid] = {
            "id": jid, "name": r.get("name", "video"), "path": None,
            "size": r.get("size", 0), "dir": Path(r.get("dir") or (VIDEO_DIR / jid)),
            "state": state, "message": r.get("message", ""),
            "progress": 100 if state == "done" else 0,
            "info": r.get("info"), "checks": r.get("checks"),
            "output": r.get("output"), "created": r.get("created") or time.time(),
            "proc": None, "logs": [], "error": None,
            "diagnoseState": "done" if r.get("diagnose") else "idle",
            "diagnoseMessage": "", "diagnoseError": None,
            "diagnose": r.get("diagnose", {}),
        }


# ---------------------------------------------------------------- AI 剪辑
def srt_time_to_seconds(t):
    t = t.strip().replace(",", ".")
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def seconds_to_srt(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def ai_plan_clips(job, instruction, max_clips, clip_length):
    path = job.get("sub") or job.get("raw")
    if not path:
        raise RuntimeError("还没有字幕，请先转录")
    cues = parse_srt(read_text(Path(path)))
    if not cues:
        raise RuntimeError("字幕解析失败")
    key = get_deepseek_key()
    if not key:
        raise RuntimeError("请先配置 AI API Key")

    rows = []
    for c in cues:
        a, b = c["time"].split("-->")
        rows.append(f'{c["i"]}\t{srt_time_to_seconds(a):.2f}\t{srt_time_to_seconds(b):.2f}\t{c["text"]}')
    transcript = "\n".join(rows)[:45000]

    system = "你是资深短视频剪辑师，只输出 JSON，不要输出解释。"
    user = (
        "下面是视频字幕（序号 / 开始秒 / 结束秒 / 文本）。请根据要求挑选最值得剪的片段。\n"
        f"要求：{instruction}\n"
        f"最多选 {max_clips} 段，每段大约 {clip_length} 秒（可在 0.5x-1.5x 之间浮动）。\n"
        "片段之间不要重叠，时间必须在字幕范围内。\n"
        '返回 JSON：{"clips":[{"start":12.3,"end":25.6,"title":"简短中文标题","reason":"为什么选它"}]}\n'
        f"字幕：\n{transcript}"
    )
    raw = llm_chat(key, system, user, timeout=180)
    obj = parse_json_loose(raw)
    raw_clips = obj.get("clips", []) if isinstance(obj, dict) else []
    duration = job.get("duration") or max(
        srt_time_to_seconds(c["time"].split("-->")[1]) for c in cues)

    clips = []
    for x in raw_clips[:max_clips]:
        try:
            s = float(x["start"])
            e = float(x["end"])
        except (KeyError, TypeError, ValueError):
            continue
        s = max(0.0, s)
        e = min(duration, e)
        if e - s < 0.5:
            continue
        clips.append({
            "start": round(s, 2), "end": round(e, 2),
            "title": str(x.get("title", "")).strip() or "片段",
            "reason": str(x.get("reason", "")).strip(),
        })
    if not clips:
        raise RuntimeError("AI 没有返回有效片段，请换个要求重试")
    return clips


def write_cut_srt(job, clips, out_path):
    cues = parse_srt(read_text(Path(job.get("sub") or job.get("raw"))))
    out = []
    offset = 0.0
    idx = 1
    for clip in clips:
        s, e = float(clip["start"]), float(clip["end"])
        for cue in cues:
            a, b = cue["time"].split("-->")
            cs, ce = srt_time_to_seconds(a), srt_time_to_seconds(b)
            if ce <= s or cs >= e:
                continue
            ns = max(cs, s) - s + offset
            ne = min(ce, e) - s + offset
            if ne - ns < 0.05:
                continue
            out.append({"i": idx,
                        "time": f"{seconds_to_srt(ns)} --> {seconds_to_srt(ne)}",
                        "text": cue["text"]})
            idx += 1
        offset += e - s
    Path(out_path).write_text(build_srt(out), encoding="utf-8")


def run_ai_edit(job, clips, options):
    try:
        if not job.get("path") or not Path(job["path"]).exists():
            raise RuntimeError("原视频已被清理，请重新上传并保留原片")
        job["aiEditState"] = "running"
        job["aiEditProgress"] = 0
        job["aiEditMessage"] = "准备剪辑…"
        job["aiEditError"] = None
        job["aiEditOutput"] = None
        job["aiEditPlan"] = clips
        persist_history()

        video = Path(job["path"])
        out_dir = job["dir"] / "AI_Edit" / ("edit_" + now_str())
        out_dir.mkdir(parents=True, exist_ok=True)
        seg_paths = []
        n = len(clips)
        total = sum(float(c["end"]) - float(c["start"]) for c in clips)

        for i, clip in enumerate(clips):
            s, e = float(clip["start"]), float(clip["end"])
            seg = out_dir / f"seg_{i:03d}.mp4"
            vf = []
            if options.get("vertical"):
                vf += ["scale=1080:1920:force_original_aspect_ratio=decrease",
                       "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"]
            vf += ["fps=30", "format=yuv420p"]
            cmd = ["ffmpeg", "-y", "-ss", f"{s:.3f}", "-to", f"{e:.3f}", "-i", str(video),
                   "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                   "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
                   "-movflags", "+faststart", str(seg)]
            run_ffmpeg_progress(job, cmd, e - s, f"剪辑片段 {i + 1}/{n}")
            seg_paths.append(seg)
            job["aiEditProgress"] = int((i + 1) / n * 80)
            job["aiEditMessage"] = f"已完成 {i + 1}/{n} 段"

        list_file = out_dir / "list.txt"
        list_file.write_text("\n".join(f"file '{p.name}'" for p in seg_paths), encoding="utf-8")
        concat = out_dir / "concat.mp4"
        job["aiEditMessage"] = "拼接片段…"
        run_ffmpeg_progress(
            job,
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(list_file), "-c", "copy", str(concat)],
            total, "拼接中", cwd=str(out_dir),
        )

        final = concat
        if options.get("burnSubtitles") or options.get("normalizeAudio"):
            job["aiEditProgress"] = 85
            job["aiEditMessage"] = "生成最终成片…"
            cmd = ["ffmpeg", "-y", "-i", str(concat)]
            if options.get("burnSubtitles"):
                cut_srt = out_dir / "cut.srt"
                write_cut_srt(job, clips, cut_srt)
                cmd += ["-vf", f"subtitles={cut_srt.name}"]
            if options.get("normalizeAudio"):
                cmd += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                    str(out_dir / "final.mp4")]
            run_ffmpeg_progress(job, cmd, total, "合成中", cwd=str(out_dir))
            final = out_dir / "final.mp4"

        job["aiEditOutput"] = str(final)
        job["aiEditState"] = "done"
        job["aiEditProgress"] = 100
        job["aiEditMessage"] = "AI 剪辑完成"
        persist_history()
    except Exception as e:  # noqa: BLE001
        job["aiEditState"] = "error"
        job["aiEditError"] = str(e)
        job["aiEditMessage"] = "剪辑失败：" + str(e)
        persist_history()


def start_ai_edit(job, clips, options):
    if job.get("aiEditState") == "running":
        return False
    job["aiEditState"] = "running"
    job["aiEditProgress"] = 0
    job["aiEditMessage"] = "准备剪辑…"
    persist_history()
    threading.Thread(target=run_ai_edit, args=(job, clips, options), daemon=True).start()
    return True


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "SubtitleApp/2.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, code, text, ctype="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file_download(self, path: Path, download_name: str):
        if not path.exists():
            return self.send_json(404, {"error": "文件不存在"})
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition",
                         "attachment; filename*=UTF-8''" + urllib.parse.quote(download_name))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(256 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    # ------------------------- GET
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            html = (APP_DIR / "index.html").read_text(encoding="utf-8")
            return self.send_text(200, html, "text/html; charset=utf-8")
        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/health":
            return self.send_json(200, {"ok": True, "jobs": len(JOBS)})
        if path == "/api/jobs":
            with JOBS_LOCK:
                jobs = sorted(JOBS.values(), key=lambda j: j["created"], reverse=True)[:20]
            return self.send_json(200, {"jobs": [job_public(j) for j in jobs]})
        if path == "/api/history":
            with JOBS_LOCK:
                jobs = sorted(JOBS.values(), key=lambda j: j.get("created") or 0, reverse=True)
            return self.send_json(200, {"jobs": [job_public(j) for j in jobs]})
        if path == "/api/video/jobs":
            with VIDEO_LOCK:
                jobs = sorted(VIDEO_JOBS.values(), key=lambda j: j.get("created") or 0, reverse=True)[:20]
            return self.send_json(200, {"jobs": [video_job_public(j) for j in jobs]})
        if path == "/api/video/status":
            job = VIDEO_JOBS.get((qs.get("job") or [""])[0])
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            return self.send_json(200, video_job_public(job))
        if path == "/api/video/download":
            job = VIDEO_JOBS.get((qs.get("job") or [""])[0])
            if not job or not job.get("output") or not Path(job["output"]).exists():
                return self.send_json(404, {"error": "优化视频尚未生成"})
            out = Path(job["output"])
            return self.send_file_download(out, out.name)
        if path == "/api/ai-edit/download":
            job = JOBS.get((qs.get("job") or [""])[0])
            if not job or not job.get("aiEditOutput") or not Path(job["aiEditOutput"]).exists():
                return self.send_json(404, {"error": "成片尚未生成"})
            out = Path(job["aiEditOutput"])
            base = os.path.splitext(job["name"])[0]
            return self.send_file_download(out, base + "_ai_edit.mp4")
        if path == "/api/video/open":
            job = VIDEO_JOBS.get((qs.get("job") or [""])[0])
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            folder = Path(job["output"]).parent if job.get("output") else job["dir"]
            try:
                os.startfile(str(folder))  # noqa: S606
            except Exception as e:  # noqa: BLE001
                return self.send_json(500, {"error": str(e)})
            return self.send_json(200, {"ok": True})
        if path == "/api/env":
            force = (qs.get("refresh") or ["0"])[0] in ("1", "true")
            env = check_env(force=force)
            return self.send_json(200, env)
        if path == "/api/config":
            cfg = load_config()
            provider = (cfg.get("llmProvider") or "deepseek").lower()
            dk = (cfg.get("deepseekKey") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
            qk = (cfg.get("dashscopeKey") or os.environ.get("DASHSCOPE_API_KEY") or "").strip()
            active = qk if provider == "qwen" else dk
            return self.send_json(200, {
                "provider": provider,
                "hasKey": bool(active), "masked": mask_key(active),
                "deepseekHasKey": bool(dk), "deepseekMasked": mask_key(dk),
                "qwenHasKey": bool(qk), "qwenMasked": mask_key(qk),
            })
        if path == "/api/status":
            job = JOBS.get((qs.get("job") or [""])[0])
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            return self.send_json(200, job_public(job))
        if path == "/api/download":
            job = JOBS.get((qs.get("job") or [""])[0])
            lang = (qs.get("lang") or ["sub"])[0]
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            mapping = {
                "sub": (job.get("sub"), "_subtitle.srt"),
                "raw": (job.get("raw"), "_source.srt"),
                "reviewed": (job.get("reviewed"), "_reviewed.srt"),
            }
            f, suffix = mapping.get(lang, (None, ".srt"))
            if not f:
                return self.send_json(404, {"error": "文件尚未生成"})
            base = os.path.splitext(job["name"])[0]
            return self.send_file_download(Path(f), base + suffix)
        if path == "/api/open":
            job = JOBS.get((qs.get("job") or [""])[0])
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            folder = Path(job["sub"]).parent if job.get("sub") else job["dir"]
            try:
                os.startfile(str(folder))  # noqa: S606
            except Exception as e:  # noqa: BLE001
                return self.send_json(500, {"error": str(e)})
            return self.send_json(200, {"ok": True})
        return self.send_json(404, {"error": "Not found"})

    # ------------------------- POST
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/api/upload":
            return self.handle_upload(qs)
        if path == "/api/video/upload":
            return self.handle_video_upload(qs)

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            data = {}

        if path == "/api/recommend":
            return self.send_json(200, recommend_model(
                data.get("preference", "balanced"), data.get("sourceLang", "auto")))

        if path == "/api/config":
            cfg = load_config()
            if "deepseekKey" in data:
                k = str(data.get("deepseekKey") or "").strip()
                if k:
                    cfg["deepseekKey"] = k
                else:
                    cfg.pop("deepseekKey", None)
            if "dashscopeKey" in data:
                k = str(data.get("dashscopeKey") or "").strip()
                if k:
                    cfg["dashscopeKey"] = k
                else:
                    cfg.pop("dashscopeKey", None)
            if "llmProvider" in data:
                p = str(data.get("llmProvider") or "").strip().lower()
                if p in ("deepseek", "qwen"):
                    cfg["llmProvider"] = p
            save_config(cfg)
            _ENV_CACHE["data"] = None
            provider, active = get_llm()
            return self.send_json(200, {
                "ok": True, "provider": provider,
                "hasKey": bool(active), "masked": mask_key(active),
            })

        if path == "/api/adopt":
            dirname = str(data.get("dir", "")).strip()
            try:
                target = (WORK_DIR / dirname).resolve()
                target.relative_to(WORK_DIR.resolve())
            except Exception:
                return self.send_json(400, {"error": "非法目录"})
            if not target.is_dir():
                return self.send_json(404, {"error": "目录不存在"})
            vids = [f for f in target.rglob("*")
                    if f.is_file() and f.suffix.lower() in VIDEO_EXT]
            if not vids:
                return self.send_json(404, {"error": "目录里没有视频"})
            video = max(vids, key=lambda f: f.stat().st_size)
            job = new_job(video.name)
            job["path"] = str(video)
            job["size"] = video.stat().st_size
            set_state(job, "uploaded", "已接管现有视频，准备开始…", 0)
            return self.send_json(200, {"jobId": job["id"], "name": video.name,
                                        "size": job["size"], "reused": True})

        if path == "/api/start":
            job = JOBS.get(data.get("jobId", ""))
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            if not job.get("path"):
                return self.send_json(400, {"error": "视频未上传完成"})

            p = job.get("params") or {}
            src = data.get("sourceLang", p.get("sourceLang", "auto"))
            tgt = data.get("targetLang", p.get("targetLang", "same"))
            model = data.get("model", p.get("model", "large-v3"))
            device = data.get("device", p.get("device", "cuda"))
            compute = data.get("computeType", p.get("computeType", "float16"))
            engine = data.get("engine", p.get("engine", "auto"))
            if engine not in ("auto", "whisper", "sensevoice"):
                return self.send_json(400, {"error": "转录引擎无效"})
            if engine == "sensevoice" and src not in ("auto", "zh", "yue", "en", "ja", "ko"):
                return self.send_json(400, {"error": "SenseVoice 不支持该语言，请改用 Whisper"})
            if src not in LANGUAGES:
                return self.send_json(400, {"error": "视频语言无效"})
            if tgt != "same" and tgt not in LANGUAGES:
                return self.send_json(400, {"error": "字幕语言无效"})
            if model not in MODELS:
                return self.send_json(400, {"error": "模型无效"})
            if device not in ("cpu", "cuda"):
                return self.send_json(400, {"error": "设备无效"})

            need_llm = tgt not in ("same", "en") and tgt != src
            key = str(data.get("deepseekKey", "")).strip() or get_deepseek_key()
            if need_llm and not key:
                return self.send_json(400, {"error": "翻译到该语言需要 DeepSeek API Key，请先在设置中填写"})

            job["params"] = {
                "sourceLang": src, "targetLang": tgt, "model": model,
                "device": device, "computeType": compute, "engine": engine,
                "keepVideo": bool(data.get("keepVideo", p.get("keepVideo", False))),
                "deepseekKey": key if need_llm else "",
                "initialPrompt": str(data.get("initialPrompt", p.get("initialPrompt", ""))).strip(),
                "hotwords": str(data.get("hotwords", p.get("hotwords", ""))).strip(),
            }
            ok = start_job(job)
            return self.send_json(200, {"ok": ok})

        if path == "/api/cancel":
            job = JOBS.get(data.get("jobId", ""))
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            cancel_job(job)
            return self.send_json(200, {"ok": True})

        if path == "/api/review":
            job = JOBS.get(data.get("jobId", ""))
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            if not (job.get("sub") or job.get("raw")):
                return self.send_json(400, {"error": "字幕尚未生成"})
            if job.get("reviewState") == "running":
                return self.send_json(200, {"ok": True, "already": True})
            key = str(data.get("apiKey", "")).strip() or get_deepseek_key()
            if not key:
                return self.send_json(400, {"error": "请先在设置中填写 DeepSeek API Key"})
            job["reviewState"] = "running"
            job["reviewReport"] = None
            job["reviewError"] = None
            job["reviewed"] = None
            threading.Thread(target=run_review, args=(job, key), daemon=True).start()
            return self.send_json(200, {"ok": True})

        if path == "/api/history/delete":
            job = JOBS.get(data.get("jobId", ""))
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            shutil.rmtree(job["dir"], ignore_errors=True)
            with JOBS_LOCK:
                JOBS.pop(job["id"], None)
            persist_history()
            return self.send_json(200, {"ok": True})

        if path == "/api/history/clear":
            removed = 0
            for jid, job in list(JOBS.items()):
                if job["state"] in ("done", "error", "interrupted"):
                    shutil.rmtree(job["dir"], ignore_errors=True)
                    with JOBS_LOCK:
                        JOBS.pop(jid, None)
                    removed += 1
            persist_history()
            return self.send_json(200, {"ok": True, "removed": removed})

        if path == "/api/publish":
            job = JOBS.get(data.get("jobId", ""))
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            if not (job.get("sub") or job.get("raw")):
                return self.send_json(400, {"error": "字幕尚未生成"})
            key = str(data.get("apiKey", "")).strip() or get_deepseek_key()
            if not key:
                return self.send_json(400, {"error": "请先在设置中填写 DeepSeek API Key"})
            plats = data.get("platforms") or list(PLATFORMS.keys())
            plats = [p for p in plats if p in PLATFORMS] or list(PLATFORMS.keys())
            ok = start_publish(job, key, plats)
            return self.send_json(200, {"ok": ok})

        if path == "/api/publish/adjust":
            job = JOBS.get(data.get("jobId", ""))
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            if not job.get("publish"):
                return self.send_json(400, {"error": "请先生成一次文案"})
            instruction = str(data.get("instruction", "")).strip()
            if not instruction:
                return self.send_json(400, {"error": "请输入调整要求"})
            key = str(data.get("apiKey", "")).strip() or get_deepseek_key()
            if not key:
                return self.send_json(400, {"error": "请先在设置中填写 DeepSeek API Key"})
            plats = data.get("platforms") or list((job.get("publish") or {}).keys())
            plats = [p for p in plats if p in PLATFORMS] or list(PLATFORMS.keys())
            ok = start_publish_adjust(job, key, plats, instruction)
            return self.send_json(200, {"ok": ok})

        if path == "/api/video/optimize":
            job = VIDEO_JOBS.get(data.get("jobId", ""))
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            platform = data.get("platform", "reddit")
            if platform not in PLATFORM_VIDEO_SPECS:
                return self.send_json(400, {"error": "平台无效"})
            if not job.get("info"):
                return self.send_json(400, {"error": "视频尚未分析完成"})
            mode = data.get("mode", "auto")
            if mode not in ("auto", "remux", "encode"):
                return self.send_json(400, {"error": "模式无效"})
            ok = start_optimize(job, platform, mode)
            return self.send_json(200, {"ok": ok})

        if path == "/api/video/diagnose":
            job = VIDEO_JOBS.get(data.get("jobId", ""))
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            platform = data.get("platform", "reddit")
            if platform not in PLATFORM_VIDEO_SPECS:
                return self.send_json(400, {"error": "平台无效"})
            if not job.get("info"):
                return self.send_json(400, {"error": "视频尚未分析完成"})
            key = str(data.get("apiKey", "")).strip() or get_deepseek_key()
            if not key:
                return self.send_json(400, {"error": "请先在设置中填写 DeepSeek API Key"})
            ok = start_video_diagnose(job, platform, key)
            return self.send_json(200, {"ok": ok})

        if path == "/api/ai-edit/plan":
            job = JOBS.get(data.get("jobId", ""))
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            if not (job.get("sub") or job.get("raw")):
                return self.send_json(400, {"error": "请先完成转录"})
            try:
                clips = ai_plan_clips(
                    job,
                    str(data.get("instruction", "")).strip() or "挑选最精彩、最完整的片段",
                    int(data.get("maxClips", 5)),
                    float(data.get("clipLength", 20)),
                )
                job["aiEditPlan"] = clips
                persist_history()
                return self.send_json(200, {"clips": clips})
            except Exception as e:  # noqa: BLE001
                return self.send_json(500, {"error": str(e)})

        if path == "/api/ai-edit/render":
            job = JOBS.get(data.get("jobId", ""))
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            clips = data.get("clips") or job.get("aiEditPlan")
            if not clips:
                return self.send_json(400, {"error": "请先生成剪辑方案"})
            options = {
                "vertical": bool(data.get("vertical", False)),
                "burnSubtitles": bool(data.get("burnSubtitles", True)),
                "normalizeAudio": bool(data.get("normalizeAudio", True)),
            }
            ok = start_ai_edit(job, clips, options)
            return self.send_json(200, {"ok": ok})

        if path == "/api/cleanup":
            removed = 0
            for jid, job in list(JOBS.items()):
                if job["state"] in ("done", "error"):
                    shutil.rmtree(job["dir"], ignore_errors=True)
                    with JOBS_LOCK:
                        JOBS.pop(jid, None)
                    removed += 1
            return self.send_json(200, {"ok": True, "removed": removed})

        return self.send_json(404, {"error": "Not found"})

    def handle_upload(self, qs):
        name = safe_name((qs.get("name") or ["video.mp4"])[0])
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return self.send_json(400, {"error": "空文件"})

        job = new_job(name)
        dest = job["dir"] / name
        job["size"] = length
        remaining = length
        try:
            with open(dest, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
        except Exception as e:  # noqa: BLE001
            job["error"] = str(e)
            set_state(job, "error", "上传失败：" + str(e))
            return self.send_json(500, {"error": str(e)})

        if remaining > 0:
            set_state(job, "error", "上传中断")
            return self.send_json(400, {"error": "上传中断"})

        job["path"] = str(dest)
        job["duration"] = probe_duration(dest)
        set_state(job, "uploaded", "上传完成，准备开始…", 0)
        return self.send_json(200, {"jobId": job["id"], "name": name, "size": length})

    def handle_video_upload(self, qs):
        name = safe_name((qs.get("name") or ["video.mp4"])[0])
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return self.send_json(400, {"error": "空文件"})

        job = new_video_job(name)
        dest = job["dir"] / name
        job["size"] = length
        remaining = length
        try:
            with open(dest, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
        except Exception as e:  # noqa: BLE001
            job["state"] = "error"
            job["message"] = "上传失败：" + str(e)
            return self.send_json(500, {"error": str(e)})

        if remaining > 0:
            job["state"] = "error"
            job["message"] = "上传中断"
            return self.send_json(400, {"error": "上传中断"})

        job["path"] = str(dest)
        try:
            info = probe_video(dest)
            job["info"] = info
            job["checks"] = {p: check_video_spec(info, p) for p in PLATFORM_VIDEO_SPECS}
            job["state"] = "ready"
            job["message"] = "已分析完成，选择平台后可优化"
        except Exception as e:  # noqa: BLE001
            job["state"] = "error"
            job["message"] = "分析失败：" + str(e)
            return self.send_json(500, {"error": str(e)})

        persist_video_history()
        return self.send_json(200, {
            "jobId": job["id"], "name": name, "size": length,
            "info": info, "checks": job["checks"],
        })


# ---------------------------------------------------------------- 启动
def pick_port():
    for port in range(START_PORT, END_PORT + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"{START_PORT}-{END_PORT} 端口都被占用")


def cleanup_old_work(days=7):
    cutoff = time.time() - days * 86400
    for d in WORK_DIR.iterdir():
        if not d.is_dir():
            continue
        try:
            if d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def kill_orphan_workers():
    """上次应用被强杀时，转录子进程可能残留；启动时清理，避免抢 GPU。"""
    try:
        subprocess.run(["taskkill", "/F", "/IM", "faster-whisper-xxl.exe"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass


def main():
    kill_orphan_workers()
    cleanup_old_work()
    load_history()
    load_video_history()
    port = pick_port()
    server = ThreadingHTTPServer((HOST, port), Handler)
    server.daemon_threads = True
    url = f"http://{HOST}:{port}/"
    print("=" * 60)
    print("  视频字幕生成器已启动")
    print(f"  打开: {url}")
    print("  关闭此窗口即停止服务")
    print("=" * 60, flush=True)
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
