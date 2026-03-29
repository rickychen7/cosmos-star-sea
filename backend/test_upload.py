# -*- coding: utf-8 -*-
"""
Flow:
  1) /upload_canvas     ← 前端把画布 base64 发上来，存到 ~/Desktop/test_upload 为 image{N}.png
  2) /generate          ← 用最新的本地图片调用 /Users/liqichen/Desktop/liblib_test.py，返回生成结果 URL
  3) /uploads/<name>    ← 本地静态访问（给前端预览/二维码用；仅本机可访问）
Notes:
  - 不再使用 Postimages。
  - /generate 通过子进程调用你现有的 liblib_test.py，不强改你的脚本结构。
  - 若 liblib_test.py 需要“公网 URL”作为输入，请在脚本里自行把本地路径转成可用的远程地址（或改用支持 base64 的工作流）。
"""

import os
import re
import io
import glob
import json
import time
import base64
import shlex
import traceback
import subprocess
from typing import Optional, Tuple
from flask import Flask, request, jsonify, send_from_directory, make_response

# ---------- 基本配置 ----------
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
UPLOAD_DIR = os.path.join(DESKTOP, "test_upload")             # 你指定的桌面文件夹
os.makedirs(UPLOAD_DIR, exist_ok=True)

LIBLIB_SCRIPT = "/Users/liqichen/Desktop/liblib_test.py"      # 你现有的脚本
PYTHON_BIN    = os.environ.get("PYTHON_BIN") or os.environ.get("CONDA_PYTHON_EXE") or "python3"

LATEST_RESULT_FILE = os.path.join(UPLOAD_DIR, "_latest_result.txt")  # 记录最近一次生成的结果 URL

# ---------- Flask ----------
app = Flask(__name__)

# 允许从 file:// 或本地页面跨域调用
@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp

@app.route("/uploads/<path:filename>")
def serve_uploads(filename):
    return send_from_directory(UPLOAD_DIR, filename)

@app.route("/health")
def health():
    return jsonify({"ok": True, "upload_dir": UPLOAD_DIR, "liblib_script": LIBLIB_SCRIPT})

# ---------- 工具函数 ----------
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")

def _list_indexed_images() -> list:
    """列出形如 image{N}.ext 的文件，返回绝对路径列表。"""
    paths = []
    for ext in IMG_EXTS:
        paths.extend(glob.glob(os.path.join(UPLOAD_DIR, f"image*{ext}")))
    return paths

def _next_index() -> int:
    """计算下一个递增编号（image{N}）。"""
    mx = 0
    for p in _list_indexed_images():
        name = os.path.basename(p)
        m = re.match(r"image(\d+)\.(png|jpg|jpeg|webp)$", name, re.I)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx + 1

def _save_base64_image(data_url: str) -> Tuple[str, str]:
    """
    将 data:image/...;base64,xxx 保存到 UPLOAD_DIR，文件名 image{N}.ext
    返回 (abs_path, filename)
    """
    assert data_url.startswith("data:image/"), "invalid data url"
    header, b64 = data_url.split(",", 1)
    # ext
    ext = header.split("/")[1].split(";")[0].lower()
    if ext not in ("png", "jpg", "jpeg", "webp"):
        ext = "png"
    idx = _next_index()
    filename = f"image{idx}.{ext}"
    abs_path = os.path.join(UPLOAD_DIR, filename)
    with open(abs_path, "wb") as f:
        f.write(base64.b64decode(b64))
    return abs_path, filename

def _latest_image_path() -> Optional[str]:
    """返回目录里最近修改的一张图片绝对路径（找不到返回 None）"""
    candidates = _list_indexed_images()
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)

def _extract_any_url(text: str) -> Optional[str]:
    """
    从 stdout/stderr 中提取一个看起来像图片/资源的 http(s) URL。
    """
    if not text:
        return None
    # 优先匹配图片扩展；其次任意 http(s) 链接
    m = re.search(r"https?://[^\s\"']+\.(?:png|jpg|jpeg|webp)(?:\?[^\s\"']*)?", text, re.I)
    if m:
        return m.group(0)
    m2 = re.search(r"https?://[^\s\"']+", text)
    return m2.group(0) if m2 else None

def _call_liblib(input_path: str, emotion: str = "", basic_prompt: str = "") -> Tuple[bool, str, str]:
    """
    以子进程方式调用你的 liblib_test.py。
    约定优先：
      1) 尝试 CLI:  liblib_test.py --input <path> --emotion <label> --basic "<prompt>"
      2) 若脚本不支持以上参数，仍会调用脚本，同时通过环境变量传递，脚本可选择读取：
         - LIBLIB_INPUT_PATH
         - LIBLIB_EMOTION
         - LIBLIB_BASIC_PROMPT
    返回: (ok, result_url_or_errmsg, raw_stdout_tail)
    """
    # 优先 CLI 方式
    cmd = [
        PYTHON_BIN, LIBLIB_SCRIPT,
        "--input", input_path
    ]
    if emotion:
        cmd += ["--emotion", str(emotion)]
    if basic_prompt:
        cmd += ["--basic", basic_prompt]

    env = os.environ.copy()
    env["LIBLIB_INPUT_PATH"] = input_path
    if emotion:
        env["LIBLIB_EMOTION"] = str(emotion)
    if basic_prompt:
        env["LIBLIB_BASIC_PROMPT"] = basic_prompt

    try:
        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=600  # 给足时间
        )
    except Exception as e:
        return False, f"subprocess_error: {e}", ""

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    tail = (stdout + "\n" + stderr)[-2000:]  # 回传最后 2000 字符便于调试

    # 1) 先尝试解析 JSON 输出
    try:
        # 支持脚本打印 {"result_url": "..."} 或 {"data": {"imageUrl": "..."}}
        j = json.loads(stdout.strip())
        url = (j.get("result_url")
               or (j.get("data") or {}).get("imageUrl")
               or j.get("url")
               or (j.get("result") or {}).get("url"))
        if url:
            return True, url, tail
    except Exception:
        pass

    # 2) 尝试在输出里抓任意 URL
    url = _extract_any_url(stdout) or _extract_any_url(stderr)
    if proc.returncode == 0 and url:
        return True, url, tail

    # 3) 失败
    return False, f"liblib_failed(code={proc.returncode})", tail

# ---------- 路由：上传 + 触发生成 ----------
@app.route("/upload_canvas", methods=["POST", "OPTIONS"])
def upload_canvas():
    if request.method == "OPTIONS":
        return make_response("", 204)
    try:
        payload = request.get_json(silent=True) or {}
        data_url = payload.get("image")
        if not data_url or not data_url.startswith("data:image/"):
            return jsonify({"error": "invalid_image_data"}), 400

        abs_path, filename = _save_base64_image(data_url)
        local_url = f"http://127.0.0.1:5001/uploads/{filename}"

        return jsonify({
            "ok": True,
            "filename": filename,
            "local_path": abs_path,
            "local_url": local_url,
            "next_index_hint": _next_index()
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "server_exception", "msg": str(e)}), 500

@app.route("/generate", methods=["POST", "OPTIONS"])
def generate():
    if request.method == "OPTIONS":
        return make_response("", 204)

    # emotion: 可传 1/2/3/4 或中文标签（严肃/高兴/惊讶/皱眉）
    body = request.get_json(silent=True) or {}
    emotion = str(body.get("emotion") or "")
    basic_prompt = body.get("basic_prompt") or ""   # 如需拼接给脚本

    # 取最新图片
    input_path = _latest_image_path()
    if not input_path:
        return jsonify({"error": "no_input_image", "hint": f"请先上传画布到 {UPLOAD_DIR}"}), 400

    ok, result, tail = _call_liblib(input_path=input_path, emotion=emotion, basic_prompt=basic_prompt)

    # 调试信息（必要时你可以在终端看到）
    print("[generate] input :", input_path)
    print("[generate] emotion:", emotion, " basic_prompt:", basic_prompt)
    print("[generate] ok/url?:", ok, result)
    if tail:
        print("[generate] tail :", tail)

    if ok:
        # 记录最近一次结果 URL，方便前端轮询/恢复
        try:
            with open(LATEST_RESULT_FILE, "w") as f:
                f.write(result)
        except Exception:
            pass
        return jsonify({"ok": True, "result_url": result})
    else:
        return jsonify({"ok": False, "error": result, "tail": tail}), 500

@app.route("/latest_result")
def latest_result():
    if not os.path.exists(LATEST_RESULT_FILE):
        return jsonify({"error": "no_result"}), 404
    with open(LATEST_RESULT_FILE, "r") as f:
        url = f.read().strip()
    return jsonify({"ok": True, "result_url": url})

if __name__ == "__main__":
    # 建议用 127.0.0.1（本机），端口 5001；如需跨设备演示请改 host.
    app.run(host="127.0.0.1", port=5001, debug=True)
