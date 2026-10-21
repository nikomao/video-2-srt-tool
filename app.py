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

HOST = "127.0.0.1"
START_PORT = 8000
END_PORT = 8020
MAX_LOG = 600
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
CREATE_NO_WINDOW = 0x08000000

LANGUAGES = {
    "auto": "自动检测",
    "ja": "日语",
    "en": "英语",
    "zh": "中文",
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
    "large-v3": {"label": "large-v3 · 质量最佳", "vram": 5000},
    "medium":   {"label": "medium · 均衡",     "vram": 2500},
    "small":    {"label": "small · 快速",       "vram": 1500},
    "base":     {"label": "base · 极速",        "vram": 800},
}

PLATFORMS = {
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "instagram": "Instagram Reels",
    "x": "X (Twitter)",
    "facebook": "Facebook",
    "bilibili": "Bilibili",
}

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


def get_deepseek_key():
    return (load_config().get("deepseekKey") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()


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


def check_env(force=False):
    if not force and _ENV_CACHE["data"] and time.time() - _ENV_CACHE["ts"] < 20:
        return _ENV_CACHE["data"]

    exe = find_exe()
    gpus = _nvidia_gpus()
    ram = _total_ram_gb()
    try:
        free_gb = round(shutil.disk_usage(str(APP_DIR)).free / (1024 ** 3), 1)
    except Exception:
        free_gb = 0.0
    ffmpeg = shutil.which("ffmpeg")
    models = installed_models()
    key = get_deepseek_key()

    items = [
        {"key": "python", "label": "Python 运行环境", "status": "ok",
         "detail": sys.version.split()[0]},
        {"key": "tool", "label": "转录引擎 faster-whisper-xxl",
         "status": "ok" if exe else "warn",
         "detail": str(exe) if exe else "未安装，首次生成时会自动下载（约 1～2GB）"},
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
        {"key": "deepseek", "label": "DeepSeek API Key",
         "status": "ok" if key else "warn",
         "detail": ("已配置 " + mask_key(key)) if key else "未配置（字幕校对 / 中文等翻译需要）"},
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
        "deepseekConfigured": bool(key),
        "python": sys.version.split()[0],
    }
    _ENV_CACHE["ts"] = time.time()
    _ENV_CACHE["data"] = data
    return data


def recommend_model(pref="balanced"):
    env = check_env()
    gpus = env["gpu"]
    vram = max([g["vram_mb"] for g in gpus], default=0)

    if vram >= 6000:
        device, compute = "cuda", "float16"
    elif vram >= 3500:
        device, compute = "cuda", "int8_float16"
    else:
        device, compute = "cpu", "int8"

    if pref == "quality":
        model = "large-v3"
    elif pref == "speed":
        model = "small" if vram < 2000 else "medium"
    else:
        model = "large-v3" if vram >= 5000 else ("medium" if vram >= 1500 else "small")

    if device == "cpu" and pref != "quality" and model == "large-v3":
        model = "medium"

    need = MODELS[model]["vram"]
    if vram and vram < need:
        for m in ("medium", "small", "base"):
            if MODELS[m]["vram"] <= vram:
                model = m
                break

    if gpus:
        reason = (f'检测到 {gpus[0]["name"]}（{gpus[0]["vram_mb"] / 1024:.1f}GB 显存），'
                  f'可启用 CUDA 加速。')
    else:
        reason = "未检测到可用 NVIDIA GPU，将使用 CPU（建议选择 medium/small 以控制耗时）。"
    reason += {"quality": " 已按“质量优先”选择模型。",
               "speed": " 已按“速度优先”选择模型。",
               "balanced": " 已按“均衡”选择模型。"}.get(pref, "")

    return {
        "model": model,
        "device": device,
        "computeType": compute,
        "reason": reason,
        "preference": pref,
        "options": [{"value": k, "label": v["label"]} for k, v in MODELS.items()],
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


def deepseek_chat(api_key, system, user, json_mode=True, timeout=180):
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        DEEPSEEK_URL, data=data, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    last_err = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                obj = json.loads(r.read().decode("utf-8"))
            return obj["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            last_err = f"DeepSeek API 错误 {e.code}: {body[:300]}"
            if e.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(last_err)
        except Exception as e:  # noqa: BLE001
            last_err = f"DeepSeek 请求失败: {e}"
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(last_err)
    raise RuntimeError(last_err or "DeepSeek 请求失败")


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
        raw = deepseek_chat(api_key, system, user)
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
        raw = deepseek_chat(api_key, system, user)
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


def transcribe_cmd(exe, video, source, model, device, compute, task, out_dir):
    cmd = [str(exe), str(video)]
    if source and source != "auto":
        cmd += ["--language", source]
    cmd += [
        "--model", model,
        "--model_dir", str(MODEL_DIR),
        "--device", device,
        "--compute_type", compute,
        "--output_format", "srt",
        "--beam_size", "5",
        "--condition_on_previous_text", "False",
        "--vad_filter", "False",
        "--standard",
        "--task", task,
        "--output_dir", str(out_dir),
    ]
    return cmd


def run_transcribe(job, params, task, out_dir, base, span):
    device = params["device"]
    compute = params["computeType"]
    run_video = job.get("run_path") or job["path"]
    cmd = transcribe_cmd(job["exe"], run_video, params["sourceLang"],
                         params["model"], device, compute, task, out_dir)
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
                                 params["model"], "cpu", "int8", task, out_dir)
            run_process(job, cmd, base, span)
        else:
            raise


def run_job(job):
    try:
        params = job["params"]
        job["exe"] = ensure_tool(job)
        seed_model(job, params["model"])

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

        src = params["sourceLang"]
        tgt = params["targetLang"]
        base = os.path.splitext(job["name"])[0]

        if tgt == "same" or tgt == src:
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

            key = params.get("deepseekKey") or get_deepseek_key()
            if not key:
                raise RuntimeError("翻译到该语言需要 DeepSeek API Key，请在设置中填写后重试")
            set_state(job, "translating", "AI 翻译中（DeepSeek）…", 50)
            cues = parse_srt(read_text(Path(raw)))
            translated = llm_translate(job, cues, LANGUAGES.get(tgt, tgt), key)
            sub_path = sub_dir / f"{base}_{tgt}.srt"
            sub_path.write_text(build_srt(translated), encoding="utf-8")
            job["sub"] = str(sub_path)

        set_state(job, "done", "完成", 100)
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
        "params": job.get("params"),
        "created": job.get("created"),
        "path": job.get("path"),
        "publish": job.get("publish"),
        "publishState": job.get("publishState", "idle"),
        "publishMessage": job.get("publishMessage", ""),
        "publishError": job.get("publishError"),
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
        JOBS[jid] = {
            "id": jid, "name": r.get("name", "video"), "path": r.get("path"),
            "size": r.get("size", 0), "state": state,
            "message": r.get("message", ""), "progress": r.get("progress", 0),
            "logs": [], "error": r.get("error"),
            "raw": r.get("raw"), "sub": r.get("sub"), "reviewed": r.get("reviewed"),
            "reviewState": r.get("reviewState", "idle"),
            "reviewProgress": r.get("reviewProgress", 0),
            "reviewMessage": r.get("reviewMessage", ""),
            "reviewReport": r.get("reviewReport"), "reviewError": r.get("reviewError"),
            "publish": r.get("publish"), "publishState": r.get("publishState", "idle"),
            "publishMessage": r.get("publishMessage", ""), "publishError": r.get("publishError"),
            "detectedLang": r.get("detectedLang"),
            "dir": Path(r["dir"]) if r.get("dir") else (WORK_DIR / jid),
            "created": r.get("created") or time.time(), "started": r.get("started"),
            "proc": None, "params": r.get("params"), "exe": None,
            "duration": probe_duration(r["path"]) if r.get("path") and Path(r["path"]).exists() else 0.0,
        }

    persist_history()


def run_publish(job, api_key, platforms):
    try:
        job["publishState"] = "running"
        job["publishMessage"] = "正在分析字幕并生成文案…"
        job["publishError"] = None
        path = job.get("sub") or job.get("raw")
        if not path:
            raise RuntimeError("没有可用的字幕")
        cues = parse_srt(read_text(Path(path)))
        transcript = "\n".join(c["text"] for c in cues)
        if not transcript.strip():
            raise RuntimeError("字幕内容为空")
        transcript = transcript[:30000]
        names = "、".join(PLATFORMS.get(p, p) for p in platforms)
        system = "你是资深海外视频平台运营，只输出 JSON，不要输出解释。"
        user = (
            f"根据下面的字幕内容，为这些平台生成英文标题和简介：{names}。\n"
            "要求：\n"
            "- YouTube：标题 ≤100 字符；简介 2-4 句 + 5-10 个 hashtag；不要编造时间点\n"
            "- TikTok：caption ≤150 字符，带 5 个 hashtag，口语化、有钩子\n"
            "- Instagram Reels：caption ≤300 字符，带 8 个 hashtag\n"
            "- X (Twitter)：≤280 字符（含 hashtag），一句话抓眼球\n"
            "- Facebook：标题 ≤100 字符；简介 2-3 句 + hashtag\n"
            "- Bilibili：标题 ≤80 字符；简介 2-3 句 + hashtag\n"
            "风格：抓眼球、有悬念或情绪、符合各平台习惯；不要夸大或编造事实。\n"
            '返回严格 JSON：{"youtube":{"title":"","description":""},'
            '"tiktok":{"title":"","description":""},"instagram":{"title":"","description":""},'
            '"x":{"title":"","description":""},"facebook":{"title":"","description":""},'
            '"bilibili":{"title":"","description":""}}\n'
            "TikTok/Instagram/X 的 title 可为空字符串，caption 填在 description。\n"
            f"字幕内容：\n{transcript}"
        )
        raw = deepseek_chat(api_key, system, user, timeout=240)
        job["publish"] = parse_json_loose(raw)
        job["publishState"] = "done"
        job["publishMessage"] = "文案已生成"
        persist_history()
    except Exception as e:  # noqa: BLE001
        job["publishState"] = "error"
        job["publishError"] = str(e)
        job["publishMessage"] = "生成失败：" + str(e)
        add_log(job, "PUBLISH ERROR: " + str(e))


def start_publish(job, api_key, platforms):
    if job.get("publishState") == "running":
        return False
    job["publish"] = None
    job["publishState"] = "running"
    job["publishMessage"] = "准备生成…"
    threading.Thread(target=run_publish, args=(job, api_key, platforms), daemon=True).start()
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
        if path == "/api/env":
            force = (qs.get("refresh") or ["0"])[0] in ("1", "true")
            env = check_env(force=force)
            return self.send_json(200, env)
        if path == "/api/config":
            k = get_deepseek_key()
            return self.send_json(200, {"hasKey": bool(k), "masked": mask_key(k)})
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

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            data = {}

        if path == "/api/recommend":
            return self.send_json(200, recommend_model(data.get("preference", "balanced")))

        if path == "/api/config":
            key = str(data.get("deepseekKey", "")).strip()
            cfg = load_config()
            if key:
                cfg["deepseekKey"] = key
            else:
                cfg.pop("deepseekKey", None)
            save_config(cfg)
            _ENV_CACHE["data"] = None
            return self.send_json(200, {"ok": True, "hasKey": bool(get_deepseek_key())})

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
                "device": device, "computeType": compute,
                "deepseekKey": key if need_llm else "",
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
