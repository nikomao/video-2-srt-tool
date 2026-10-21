#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频字幕生成 Web 应用（本地运行）
================================================
- 浏览器拖拽上传视频
- 调用 faster-whisper-xxl 生成：日语 SRT + 英文字幕 SRT
- 网页内直接下载字幕
- 仅使用 Python 标准库，无需 pip install

启动：双击 启动字幕网页.bat，或 python app.py
"""

import codecs
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------------------------------------------------------- 路径 / 常量
APP_DIR = Path(__file__).resolve().parent
WORK_DIR = APP_DIR / "work"
WORK_DIR.mkdir(parents=True, exist_ok=True)

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
TOOL_DIR = LOCALAPPDATA / "AnimeSubtitleTools"
TOOL_DIR.mkdir(parents=True, exist_ok=True)
MODEL_DIR = TOOL_DIR / "models"

TOOL_ARCHIVE_URL = (
    "https://github.com/Purfview/whisper-standalone-win/releases/download/"
    "Faster-Whisper-XXL/Faster-Whisper-XXL_r245.4_windows.7z"
)
SEVENZIP_URL = "https://www.7-zip.org/a/7zr.exe"

HOST = "127.0.0.1"
START_PORT = 8000
END_PORT = 8020
MAX_LOG = 500
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

JOBS = {}
JOBS_LOCK = threading.Lock()

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".flv", ".wmv", ".webm", ".m4v",
             ".mpg", ".mpeg", ".ts", ".m2ts", ".rmvb", ".3gp"}


# ---------------------------------------------------------------- 工具函数
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
    with JOBS_LOCK:
        job["state"] = state
        if message is not None:
            job["message"] = message
        if progress is not None:
            job["progress"] = progress


def new_job(name):
    jid = now_str() + "_" + os.urandom(3).hex()
    d = WORK_DIR / jid
    d.mkdir(parents=True, exist_ok=True)
    job = {
        "id": jid,
        "name": name,
        "path": None,
        "size": 0,
        "state": "uploading",
        "message": "正在上传…",
        "progress": 0,
        "logs": [],
        "error": None,
        "ja": None,
        "en": None,
        "dir": d,
        "created": time.time(),
        "started": None,
        "proc": None,
    }
    with JOBS_LOCK:
        JOBS[jid] = job
    return job


def job_public(job):
    ja = job.get("ja")
    en = job.get("en")
    return {
        "id": job["id"],
        "name": job["name"],
        "size": job["size"],
        "state": job["state"],
        "message": job["message"],
        "progress": job["progress"],
        "logs": job["logs"][-80:],
        "error": job["error"],
        "jaReady": bool(ja and Path(ja).exists()),
        "enReady": bool(en and Path(en).exists()),
        "elapsed": int(time.time() - job["started"]) if job.get("started") else 0,
    }


# ---------------------------------------------------------------- 下载 / 工具
def download_file(url, dest: Path, job):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    add_log(job, f"下载: {url}")
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
    os.replace(tmp, dest)


def find_exe():
    for p in TOOL_DIR.rglob("faster-whisper-xxl.exe"):
        return p
    return None


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
    subprocess.run(
        [str(sevenzip), "x", str(archive), f"-o{TOOL_DIR}", "-y"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
    )
    exe = find_exe()
    if not exe:
        raise RuntimeError("解压后未找到 faster-whisper-xxl.exe")
    return exe


def seed_model(job):
    """如果本机 HF 缓存里已有 large-v3，直接硬链接复用，省去 3GB 下载。"""
    target = MODEL_DIR / "large-v3"
    if (target / "model.bin").exists():
        return
    snapshots = (Path.home() / ".cache" / "huggingface" / "hub"
                 / "models--Systran--faster-whisper-large-v3" / "snapshots")
    if not snapshots.exists():
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


# ---------------------------------------------------------------- 转录执行
def run_process(job, cmd, base, span):
    env = os.environ.copy()
    env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    env.setdefault("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    add_log(job, "$ " + " ".join(f'"{c}"' if " " in c else c for c in cmd[1:]))
    proc = subprocess.Popen(
        cmd,
        cwd=str(Path(cmd[0]).parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
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
    if len(line) < 200:
        job["message"] = line


def run_job(job):
    try:
        exe = ensure_tool(job)
        seed_model(job)

        video = Path(job["path"])
        out = job["dir"] / ("Subtitles_" + now_str())
        ja_dir = out / "Japanese"
        en_dir = out / "English"
        ja_dir.mkdir(parents=True, exist_ok=True)
        en_dir.mkdir(parents=True, exist_ok=True)

        # CPU 模式，稳定优先；有 NVIDIA 显卡可改成 cuda / float16
        common = [
            str(exe), str(video),
            "--language", "ja",
            "--model", "large-v3",
            "--model_dir", str(MODEL_DIR),
            "--device", "cpu",
            "--compute_type", "int8",
            "--output_format", "srt",
            "--beam_size", "5",
            "--condition_on_previous_text", "False",
            "--vad_filter", "False",
            "--standard",
        ]

        set_state(job, "transcribing", "正在生成日语字幕…", 2)
        run_process(job, common + ["--task", "transcribe", "--output_dir", str(ja_dir)], 2, 48)
        ja = first_srt(ja_dir)
        if not ja:
            raise RuntimeError("未生成日语 SRT")
        job["ja"] = ja

        set_state(job, "translating", "正在生成英文字幕…", 52)
        run_process(job, common + ["--task", "translate", "--output_dir", str(en_dir)], 52, 46)
        en = first_srt(en_dir)
        if not en:
            raise RuntimeError("未生成英文 SRT（日语字幕已保留）")
        job["en"] = en

        set_state(job, "done", "完成", 100)
        try:
            video.unlink()  # 上传的视频副本用完即删，节省磁盘
        except OSError:
            pass
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        add_log(job, "ERROR: " + msg)
        job["error"] = msg
        set_state(job, "error", "失败：" + msg)
    finally:
        job["proc"] = None


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


# ---------------------------------------------------------------- HTTP 服务
class Handler(BaseHTTPRequestHandler):
    server_version = "SubtitleApp/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静默访问日志
        pass

    # ---------- 输出辅助 ----------
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
        self.send_header(
            "Content-Disposition",
            "attachment; filename*=UTF-8''" + urllib.parse.quote(download_name),
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(256 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    # ---------- GET ----------
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
        if path == "/api/status":
            jid = (qs.get("job") or [""])[0]
            job = JOBS.get(jid)
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            return self.send_json(200, job_public(job))
        if path == "/api/download":
            jid = (qs.get("job") or [""])[0]
            lang = (qs.get("lang") or ["ja"])[0]
            job = JOBS.get(jid)
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            f = job.get("ja") if lang == "ja" else job.get("en")
            if not f:
                return self.send_json(404, {"error": "字幕尚未生成"})
            suffix = "_ja.srt" if lang == "ja" else "_en.srt"
            base = os.path.splitext(job["name"])[0]
            return self.send_file_download(Path(f), base + suffix)
        if path == "/api/open":
            jid = (qs.get("job") or [""])[0]
            job = JOBS.get(jid)
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            folder = Path(job["ja"]).parent if job.get("ja") else job["dir"]
            try:
                os.startfile(str(folder))  # noqa: S606
            except Exception as e:  # noqa: BLE001
                return self.send_json(500, {"error": str(e)})
            return self.send_json(200, {"ok": True})
        return self.send_json(404, {"error": "Not found"})

    # ---------- POST ----------
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

        if path == "/api/start":
            job = JOBS.get(data.get("jobId", ""))
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            if not job.get("path"):
                return self.send_json(400, {"error": "视频未上传完成"})
            ok = start_job(job)
            return self.send_json(200, {"ok": ok})
        if path == "/api/cancel":
            job = JOBS.get(data.get("jobId", ""))
            if not job:
                return self.send_json(404, {"error": "任务不存在"})
            cancel_job(job)
            return self.send_json(200, {"ok": True})
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
        raw_name = (qs.get("name") or ["video.mp4"])[0]
        name = safe_name(raw_name)
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


def main():
    cleanup_old_work()
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
