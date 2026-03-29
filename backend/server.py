# -*- coding: utf-8 -*-
"""
server.py — 与原架构一致的整洁版
- /upload 保存到 Desktop/test_image_upload，顺序命名 image1.png, image2.png...
- /generate 调 liblib_test.py，解析生成图直链 -> 立即下载到 Desktop/generated，
  顺序命名 gen1.png, gen2.png...；返回 {PUBLIC_URL}/generated/genN.png
- /generated/<filename> 暴露本地生成图（前端 <img> + 二维码都用它）
- 其它路由与交互保持不变
"""

import os
import re
import json
import shlex
import traceback
import subprocess
import time # 【新增】
from typing import Optional, Tuple, List

import requests
from flask import Flask, request, jsonify, send_from_directory

# 全局状态：用于追踪当前的“情绪周期/会话”ID
CURRENT_EMOTION_ID = 0  # 初始为 0，每次上传递增
CACHED_EMOTION = "2"    # 缓存最新的实时情绪标签（从 latest_emotion.txt 获得）
# ========= 必改：每次开新 ngrok 后更新这里 =========
PUBLIC_URL = "https://YOUR_NGROK_SUBDOMAIN.ngrok-free.app"  # ← 每次启动 ngrok 后替换

# ---------- 基本目录 ----------
HOME = os.path.expanduser("~")
DESKTOP = os.path.join(HOME, "Desktop")

# 输入图目录（和你前端一致）
UPLOAD_DIR = os.path.join(DESKTOP, "test_image_upload")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 生成图目录（本地持久化）
GENERATED_FOLDER = os.path.join(DESKTOP, "generated")
os.makedirs(GENERATED_FOLDER, exist_ok=True)

# 【新增：情绪记录 JSON 文件路径】
EMOTION_LOG_FILE = os.path.join(DESKTOP, "emotion_log.json") 

# 生成脚本与 Python 解释器
LIBLIB_SCRIPT = "/Users/liqichen/Desktop/liblib_test.py"
PYTHON_BIN = (
    os.environ.get("PYTHON_BIN")
    or os.environ.get("CONDA_PYTHON_EXE")
    or "python3"
)

# 最近一次生成结果（仅便于调试/留档）
LATEST_RESULT_FILE = os.path.join(UPLOAD_DIR, "_latest_result.txt")

# ---------- Flask ----------
app = Flask(__name__)

@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp

# ---------- 工具函数 ----------
# 【新增：JSON 读写函数】
def _read_emotion_log() -> dict:
    """读取情绪记录 JSON 文件，失败返回空字典。"""
    if not os.path.exists(EMOTION_LOG_FILE):
        return {}
    try:
        with open(EMOTION_LOG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _write_emotion_log(data: dict):
    """写入情绪记录 JSON 文件。"""
    try:
        with open(EMOTION_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"❌ 写入情绪日志失败: {e}")

def _list_indexed_images() -> List[Tuple[int, str]]:
    """返回 [(编号, 文件名), ...]，仅统计 image{n}.png。"""
    out: List[Tuple[int, str]] = []
    for name in os.listdir(UPLOAD_DIR):
        if not name.lower().endswith(".png"):
            continue
        m = re.match(r"^image(\d+)\.png$", name, re.IGNORECASE)
        if m:
            out.append((int(m.group(1)), name))
    out.sort(key=lambda x: x[0])
    return out

def _latest_image_path() -> Optional[str]:
    items = _list_indexed_images()
    if not items:
        return None
    _, fname = items[-1]
    return os.path.join(UPLOAD_DIR, fname)

def _local_upload_to_public_url(local_path: str) -> str:
    """把本地 uploads 路径转成 PUBLIC_URL 可访问的 URL。"""
    fname = os.path.basename(local_path)
    return f"{PUBLIC_URL}/uploads/{fname}"

def _parse_result_url_from_stdout(stdout: str) -> Optional[str]:
    """
    从 liblib_test.py 的标准输出中解析生成图直链。
    先尝试逐行 JSON，再兜底从文本中反向匹配图片直链（优先非 /uploads/）。
    """
    # 优先逐行尝试 JSON
    for line in stdout.splitlines():
        s = line.strip()
        if not s or not (s.startswith("{") and s.endswith("}")):
            continue
        try:
            data = json.loads(s)
            if isinstance(data, dict):
                # 常见字段：result_url / url / data.images[0].imageUrl
                if data.get("result_url"):
                    return str(data["result_url"])
                if data.get("url"):
                    return str(data["url"])
                d = data.get("data")
                if isinstance(d, dict):
                    images = d.get("images")
                    if isinstance(images, list) and images:
                        img0 = images[0]
                        if isinstance(img0, dict) and img0.get("imageUrl"):
                            return str(img0["imageUrl"])
        except Exception:
            pass

    # 兜底：更稳健的反向匹配
    all_urls = re.findall(r'https?://[^\s"\'<>]+', stdout, re.IGNORECASE)
    input_domains = ("ngrok-free.app", "127.0.0.1", "localhost")
    gen_hosts_pref = ("liblibai-tmp-image.", "liblib.cloud", "openapi.liblibai.cloud")

    # 先找生成域名的图片直链
    for url in reversed(all_urls):
        low = url.lower()
        if low.endswith((".png", ".jpg", ".jpeg", ".webp")) and any(h in low for h in gen_hosts_pref):
            return url
    # 再找非 /uploads/ 且非本机域名的图片直链
    for url in reversed(all_urls):
        low = url.lower()
        if low.endswith((".png", ".jpg", ".jpeg", ".webp")) \
           and "/uploads/" not in low \
           and not any(h in low for h in input_domains):
            return url
    return None

def _call_liblib(latest_local_image: str, emotion: str = "2", basic_prompt: str = "") -> Tuple[bool, str, str]:
    """
    调 liblib_test.py：
      入参：最新上传图的 PUBLIC_URL + emotion + 可选 basic_prompt
      返回：(ok, result(or err), full_log)
        - ok=True 时，result 为生成图直链（云端临时链接）
        - ok=False 时，result 为错误信息
    """
    image_url = _local_upload_to_public_url(latest_local_image)
    print(f"[DEBUG] server.py 调用 liblib_test.py:")
    print(f"  latest_local_image: {latest_local_image}")
    print(f"  image_url: {image_url}")
    print(f"  emotion: {emotion}")
    cmd = [PYTHON_BIN, LIBLIB_SCRIPT, "--image-url", image_url, "--emotion", str(emotion)]
    if basic_prompt:
        cmd += ["--basic-prompt", basic_prompt]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=180
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        full_log = f"[CMD] {shlex.join(cmd)}\n[STDOUT]\n{stdout}\n[STDERR]\n{stderr}"

        if proc.returncode != 0:
            return False, f"liblib_test.py exit {proc.returncode}", full_log

        result_url = _parse_result_url_from_stdout(stdout)
        if not result_url:
            return False, "No result URL parsed from liblib_test.py output", full_log

        return True, result_url, full_log

    except subprocess.TimeoutExpired:
        return False, "Timeout when calling liblib_test.py", traceback.format_exc()
    except Exception as e:
        return False, f"Exception: {e}", traceback.format_exc()

def save_generated_image(temp_url: str) -> Optional[str]:
    """
    下载云端临时直链图片，保存到 Desktop/generated，顺序命名 gen{n}.png。
    成功返回文件名（如 gen3.png），失败返回 None。
    """
    try:
        existing = [
            f for f in os.listdir(GENERATED_FOLDER)
            if f.lower().startswith("gen") and f.lower().endswith(".png")
        ]
        nums = []
        for f in existing:
            m = re.match(r"^gen(\d+)\.png$", f, re.IGNORECASE)
            if m:
                nums.append(int(m.group(1)))
        next_num = (max(nums) + 1) if nums else 1
        filename = f"gen{next_num}.png"
        filepath = os.path.join(GENERATED_FOLDER, filename)

        r = requests.get(temp_url, timeout=20)
        r.raise_for_status()
        with open(filepath, "wb") as out:
            out.write(r.content)

        return filename
    except Exception as e:
        print("❌ 保存生成图失败:", e)
        return None

# ---------- 静态文件路由 ----------
@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)

@app.route("/generated/<path:filename>")
def serve_generated(filename):
    return send_from_directory(GENERATED_FOLDER, filename)

# ---------- 健康检查 ----------
@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "public_url": PUBLIC_URL,
        "upload_dir": UPLOAD_DIR,
        "generated_dir": GENERATED_FOLDER
    })

# ---------- 业务路由 ----------
@app.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "no_file"}), 400

    file = request.files["file"]
    if not file or file.filename == "":
        return jsonify({"ok": False, "error": "empty_filename"}), 400

    # 顺序命名 image{n}.png
    items = _list_indexed_images()
    next_num = (items[-1][0] + 1) if items else 1
    filename = f"image{next_num}.png"
    path = os.path.join(UPLOAD_DIR, filename)
    file.save(path)

    file_url = f"{PUBLIC_URL}/uploads/{filename}"
    return jsonify({"ok": True, "url": file_url, "filename": filename})

# 放在 server.py 路由区
@app.route("/events/latest")
def events_latest():
    data = _read_emotion_log()              # 读 emotion_log.json（你已有）
    if not data:
        return jsonify({"ok": True, "event": None})
    # 从 key 中提取 image 编号，取最大
    def parse_id(k):
        import re
        m = re.match(r"^image(\d+)\.", k)
        return int(m.group(1)) if m else -1

    max_key = max(data.keys(), key=parse_id)
    ev_id = parse_id(max_key)
    ev = data[max_key]
    return jsonify({
        "ok": True,
        "event": {
            "id": ev_id,                   # 事件编号（用 imageN 的 N）
            "emotion": int(ev.get("emotion", 2)),
            "image_url": ev.get("image_url"),
            "timestamp": ev.get("timestamp")
        }
    })

@app.route("/generate", methods=["POST"])
def generate():
    """
    1) 取最新上传图（/uploads/imageN.png 的 PUBLIC_URL）
    2) 调 liblib_test.py 得到云端生成图直链
    3) 立刻下载 -> Desktop/generated/genN.png
    4) 返回 {PUBLIC_URL}/generated/genN.png，前端用它显示 & 生成二维码
    5) 【新增】记录 imageN.png 对应的 emotion 标签
    """
    data = request.get_json(silent=True) or {}
    emotion = str(data.get("emotion", "2"))
    basic_prompt = data.get("basic_prompt", "")

    # 1. 获取最新上传图的文件名和完整路径 (替换了原有的 latest = _latest_image_path())
    items = _list_indexed_images()
    if not items:
        return jsonify({"ok": False, "error": "no_input_image"}), 400

    latest_index, latest_filename = items[-1] # <-- 获取到文件名，例如 'image1.png'
    latest_path = os.path.join(UPLOAD_DIR, latest_filename) # 完整路径，用于调用 _call_liblib

    # ===============================================
    # 【新增记录逻辑】: 将情绪标签和文件名写入 JSON 文件
    # ===============================================
    log_data = _read_emotion_log()
    log_data[latest_filename] = {
        "emotion": emotion,
        "timestamp": time.time(),
        "image_url": f"{PUBLIC_URL}/uploads/{latest_filename}" # 完整的公网URL，方便查阅
    }
    _write_emotion_log(log_data)
    # ===============================================

    # 2. 调 liblib_test.py
    ok, result, log = _call_liblib(latest_path, emotion=emotion, basic_prompt=basic_prompt)

    if ok:
        # 记录最近一次生成结果（方便留档）
        try:
            with open(LATEST_RESULT_FILE, "w") as f:
                f.write((result or "").strip())
        except Exception:
            pass

        # 保存到本地 Desktop/generated
        fname = save_generated_image(result)
        if fname:
            # ✅ 等文件真正写入完成（最多 1 秒）
            full_path = os.path.join(GENERATED_FOLDER, fname)
            for _ in range(10):
                if os.path.exists(full_path) and os.path.getsize(full_path) > 1024:
                    break
                time.sleep(0.1)

            local_url = f"{PUBLIC_URL}/generated/{fname}"
            return jsonify({"ok": True, "url": local_url})

        # 兜底：本地保存失败，仍返回临时直链（可显示但会过期）
        return jsonify({"ok": True, "url": result, "warn": "save_failed_use_temp_link"})

    # 失败：把 log 带回便于调试
    return jsonify({"ok": False, "error": result, "log": log}), 500

# ... (在 server.py 中找到 @app.route("/get_emotion", methods=["GET"]) )

@app.route("/get_emotion", methods=["GET"])
def get_emotion():
    global CACHED_EMOTION

    # 1. 实时读取 latest_emotion.txt（但不再在每次请求时打印，改为更新全局缓存）
    try:
        with open("latest_emotion.txt", "r") as f:
            emo_str = (f.read() or "").strip() 
        
        if emo_str.isdigit() and 1 <= int(emo_str) <= 4: # 假设标签是 1-4
            CACHED_EMOTION = emo_str
        # 如果文件内容无效，保持 CACHED_EMOTION 不变
        
    except FileNotFoundError:
        # 如果文件不存在，保持 CACHED_EMOTION 不变（默认是 "2"）
        pass
        
    # 2. 统一打印（仅当情绪缓存发生变化时打印可能更好，但此处按您的要求简化为每次都打印状态）
    emo_id_str = f"{CURRENT_EMOTION_ID:04d}"

    # === 修改后的打印日志：返回当前的固定 ID 和最新的实时情绪 ===
    print(f"🔄 {emo_id_str} 实时情绪追踪: 当前周期ID={emo_id_str}, 实时标签={CACHED_EMOTION}") 
    # =============================================================

    # 3. 返回固定的周期 ID 和最新的实时标签
    return jsonify({
        "ok": True, 
        "emotion": int(CACHED_EMOTION), # 实时变化的标签
        "session_id": emo_id_str       # 用户没有上传时，这个 ID 保持不变
    })

# ---------- 入口 ----------
if __name__ == "__main__":
    # 每次开新 ngrok，别忘了把上面的 PUBLIC_URL 换成新外链
    app.run(host="127.0.0.1", port=5001, debug=True)



