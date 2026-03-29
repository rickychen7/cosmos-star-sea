# -*- coding: utf-8 -*-
"""
Liblib ComfyUI 工作流调用（最终稳定版 · 仅覆盖动态节点）
————————————————————————————————————————————————————
关键点：
1) generateParams 仅覆盖运行时需要动态化的节点：
   - #16 TextEncodeQwenImageEdit.prompt  ← (基础 + 情绪)
   - #29 TextEncodeQwenImageEdit.prompt  ← (负向，可留空)
   - #27 LoadImage.image                 ← (用户图片 URL)
   其余所有节点（UNET/CLIP/VAE/采样器/连线）全部以云端 workflow 为准，避免 1002014 等校验错误。
2) 签名："{uri}&{timestamp_ms}&{uuid}" → HMAC-SHA1 → base64.urlsafe_b64encode 去 '='
3) 鉴权参数放在 Query（非 Header）：AccessKey / Signature / Timestamp / SignatureNonce
4) 提交：POST https://openapi.liblibai.cloud/api/generate/comfyui/app
5) 轮询：POST https://openapi.liblibai.cloud/api/generate/comfy/status  （generateUuid 在 body）
"""

import time
import uuid
import hmac
import base64
from hashlib import sha1
import requests
import json
from urllib.parse import urlencode

# ==== 新增：仅为动态取图所需 ====
import os
import glob
import re
import argparse

# ============= 你的密钥（请替换为自己的 LibLib API 密钥） =============
ACCESS_KEY = "YOUR_ACCESS_KEY"       # 在 https://www.liblibai.com 获取
SECRET_KEY = "YOUR_SECRET_KEY"       # 在 https://www.liblibai.com 获取

# ============= API 固定信息 =============
HOST = "https://openapi.liblibai.cloud"
URI_GEN   = "/api/generate/comfyui/app"
URI_STATE = "/api/generate/comfy/status"
URL_GEN   = f"{HOST}{URI_GEN}"
URL_STATE = f"{HOST}{URI_STATE}"

# ============= 工作流 ID（与你云端一致） =============
TEMPLATE_UUID = "4df2efa0f18d46dc9758803e478eb51c"
WORKFLOW_UUID = "85d84ced33284497bcc209cf66aeee0f"

# ============= 默认输入（后续接入画布/情绪识别再替换） =============
DEFAULT_IMAGE_URL = (
    "https://i.postimg.cc/YCY8Y0FN/2025-09-06-17-31-41.png"
)
DEFAULT_EMOTION_ID = "2"  # 1=严肃, 2=高兴, 3=惊讶, 4=皱眉

# ============= Prompt 配置（情绪动态拼接，仅作用于 #16） =============
BASE_PROMPT = (
    "将这张简笔画转化为一幅三渲二风格的未来城市设计图。\n"
    "背景设定在火星或地球之外的星球，场景充满科幻感。\n"
    "建筑采用未来主义设计，有高耸的塔楼、悬浮的建筑结构、透明穹顶和发光的能量管道。\n"
    "画面色彩鲜艳，带有强烈的金属光泽和反射效果，红色和橙色的火星地貌与城市融合。\n"
    "整体风格为三渲二，具有真实的光影和立体感，但保持动画般的色彩饱和度和清晰轮廓。\n"
    "重要：请确保完全覆盖所有白色背景区域，不要留下任何空白或未填充的区域。\n"
    "整个画面应该充满细节和色彩，从边缘到边缘都要有内容。"
)

EMOTION_PROMPTS = {
    "1": (
        "【情绪：严肃】 请让画面带有压迫感和纪念碑般的庄重气氛，突出人类在外星艰难生存的严峻现实。\n"
        "天空中笼罩着厚重的乌云或沙尘，环境荒凉而冷冽。\n"
        "建筑群展现出冷酷的秩序感，像一座星际堡垒城市，而非欢乐的乌托邦。"
    ),
    "2": (
        "【情绪：高兴】 请让画面更明亮、充满活力和欢乐氛围。\n"
        "色调以金色、橙色、蓝绿色为主，天空中有漂浮的能量粒子和流光，建筑外墙反射出耀眼的光芒。\n"
        "整个城市看起来像一个充满希望的未来乌托邦，带有节日般的氛围。"
    ),
    "3": (
        "【情绪：惊讶】 请让画面呈现出一种震撼与意外的氛围，仿佛发现了前所未见的奇迹。\n"
        "天空中闪现突如其来的能量闪电或极光，建筑群在光线映照下显得更加宏伟诡异。\n"
        "色调以紫色、蓝色和银白为主，带有强烈的光影对比和耀眼的反射效果，营造出不可思议的未来奇观。\n"
        "整个场景让人心生敬畏与惊叹，仿佛城市本身是某种超凡存在的显现。"
    ),
    "4": (
        "【情绪：皱眉】 请让画面流露出困惑与不安的氛围，仿佛未来城市隐藏着潜在的危机。\n"
        "天空灰暗，带有异常的能量波动或不规则的光影，建筑外墙出现细微的裂痕与闪烁的能量紊乱。\n"
        "色调以灰蓝色、黯淡的橙红和冷金属色为主，整体光线偏低沉。\n"
        "城市看起来既先进又脆弱，观者会感到疑惑和不安，仿佛某种隐患即将爆发。"
    ),
}
NEG_PROMPT = "白色背景, 空白区域, 未填充区域, 透明背景, 空白画布, 单调背景, 简单背景, 无细节背景"  # 负向提示词：排除白色区域

# =========================================================
#                      鉴权 & HTTP
# =========================================================

def _make_signature(uri: str, ts_ms: str, nonce: str) -> str:
    content = "&".join((uri, ts_ms, nonce))
    digest  = hmac.new(SECRET_KEY.encode("utf-8"), content.encode("utf-8"), sha1).digest()
    sign    = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    print("\n[Sign Debug]")
    print("  CanonicalString:", content)
    print("  Signature      :", sign)
    return sign

def _signed_query(uri: str, extra: dict | None = None) -> dict:
    ts    = str(int(time.time() * 1000))
    nonce = str(uuid.uuid4())
    sign  = _make_signature(uri, ts, nonce)
    q = {
        "AccessKey": ACCESS_KEY,
        "Signature": sign,
        "Timestamp": ts,
        "SignatureNonce": nonce
    }
    if extra:
        q.update(extra)
    print("[Query Debug]")
    print("  Query params:", q)
    return q

def _post_json(url: str, uri: str, *, json_body: dict | None = None, query_extra: dict | None = None) -> requests.Response:
    params  = _signed_query(uri, extra=query_extra)
    headers = {"Content-Type": "application/json"}
    print("\n[HTTP Request]")
    print("  URL :", url)
    print("  QS  :", urlencode(params))
    print("  JSON:", json.dumps(json_body or {}, ensure_ascii=False))
    r = requests.post(url, params=params, json=json_body, headers=headers, timeout=60)
    print("[HTTP Response]")
    print("  status:", r.status_code)
    try:
        print("  json  :", r.json())
    except Exception:
        print("  text  :", r.text)
    return r

# =========================================================
#               动态选择「输入图 URL」的工具
# =========================================================

DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
LOCAL_UPLOAD_DIR = os.path.join(DESKTOP, "test_image_upload")     # 你指定的本地上传文件夹
LOCAL_SERVER_PREFIX = "http://127.0.0.1:5001/uploads"             # server.py 暴露的静态访问前缀（仅本机可见）

_IMG_RE = re.compile(r"^image(\d+)\.(png|jpg|jpeg|webp|gif)$", re.IGNORECASE)

def _latest_local_image_url() -> str | None:
    """
    在 ~/Desktop/test_image_upload/ 中按「image{n}.png」最大编号取最新，
    并映射到公网地址，例如：
      https://b0aaabcef2f4.ngrok-free.app/uploads/image{n}.png
    （✅ Liblib 云端可拉取；替代 localhost）
    """
    if not os.path.isdir(LOCAL_UPLOAD_DIR):
        return None
    max_n = -1
    latest_name = None
    for f in os.listdir(LOCAL_UPLOAD_DIR):
        m = _IMG_RE.match(f)
        if not m:
            continue
        try:
            n = int(m.group(1))
        except:
            continue
        if n > max_n:
            max_n = n
            latest_name = f

    if latest_name:
        # 公网可访问的 ngrok 地址（与server.py保持一致）
        PUBLIC_SERVER_PREFIX = "https://YOUR_NGROK_SUBDOMAIN.ngrok-free.app/uploads"  # ← 替换为你的 ngrok 地址
        return f"{PUBLIC_SERVER_PREFIX}/{latest_name}"

    return None


def resolve_input_image_url(cli_image_url: str | None = None) -> str:
    """
    输入图 URL 解析优先级：
      1) CLI:        --image-url https://...
      2) ENV:        LIBLIB_INPUT_URL=https://...
      3) Local:      ~/Desktop/test_image_upload 里 image{n}.png 最大编号 → http://127.0.0.1:5001/uploads/xxx
                     （⚠️ 仅本机可见，Liblib 云端不可拉；生产请传公网 URL）
      4) DEFAULT:    DEFAULT_IMAGE_URL
    """
    print(f"[DEBUG] resolve_input_image_url called with cli_image_url: {cli_image_url}")
    if cli_image_url and cli_image_url.strip():
        print(f"[DEBUG] 使用CLI参数: {cli_image_url.strip()}")
        return cli_image_url.strip()
    env_url = (os.getenv("LIBLIB_INPUT_URL") or "").strip()
    if env_url:
        print(f"[DEBUG] 使用环境变量: {env_url}")
        return env_url
    local_url = _latest_local_image_url()
    if local_url:
        print(f"⚠️ 使用本地文件映射 URL（仅本机可见，云端不可拉取）：{local_url}")
        return local_url
    print("⚠️ 没找到有效输入图，回退 DEFAULT_IMAGE_URL")
    return DEFAULT_IMAGE_URL

# =========================================================
#                  生成 payload（仅覆盖 16/29/27）
# =========================================================

def build_payload(image_url: str, emotion_id: str = DEFAULT_EMOTION_ID) -> dict:
    """
    最终稳定策略：
    - 仅覆盖 #16 / #29 / #27 三个节点的 inputs
    - 其它节点/连线全部使用云端 workflow 的既有配置
    """
    emo = str(emotion_id).strip()
    if emo not in EMOTION_PROMPTS:
        print(f"[Warn] emotion_id={emotion_id} 非 1/2/3/4，回退到 '2'")
        emo = "2"

    final_prompt = BASE_PROMPT + "\n\n" + EMOTION_PROMPTS[emo]

    return {
        "templateUuid": TEMPLATE_UUID,
        "generateParams": {
            # 16：正向 prompt（动态）
            "16": {
                "class_type": "TextEncodeQwenImageEdit",
                "inputs": { "prompt": final_prompt },
                "widgets_values":[final_prompt]
            },
            # 29：负向 prompt（可留空）
            "29": {
                "class_type": "TextEncodeQwenImageEdit",
                "inputs": { "prompt": NEG_PROMPT },
                "widgets_values":[NEG_PROMPT]
            },
            # 27：用户图片 URL（动态）
            "27": {
                "class_type": "LoadImage",
                "inputs": { "image": image_url },
                "widgets_values":[
                    image_url,
                    "image",
                    ""
                ]
            },
            # 28：输出分辨率控制（2048x2048，更高分辨率）
            "28": {
                "class_type": "ImageScaleToTotalPixels",
                "inputs": {},
                "widgets_values": [
                    "area",  # 改用area算法，更好地处理白色区域
                    4194304  # 2048*2048 = 4194304 像素，提高分辨率
                ]
            },

            # ⚠️ 不要在本地覆盖 UNET/CLIP/VAE/采样器/连线等节点，避免 1002014 参数校验错误
            "workflowUuid": WORKFLOW_UUID
        }
    }

# =========================================================
#                       业务流程
# =========================================================

def submit_job(image_url: str, emotion_id: str = DEFAULT_EMOTION_ID) -> str | None:
    payload = build_payload(image_url=image_url, emotion_id=emotion_id)
    resp = _post_json(URL_GEN, URI_GEN, json_body=payload)
    try:
        data = resp.json()
    except Exception:
        print("❌ 提交响应非 JSON")
        return None

    if data.get("code") == 0 and data.get("data") and data["data"].get("generateUuid"):
        return data["data"]["generateUuid"]

    print("❌ 提交失败：", json.dumps(data, ensure_ascii=False))
    return None

def poll_status(gen_uuid: str):
    while True:
        resp = _post_json(URL_STATE, URI_STATE, json_body={"generateUuid": gen_uuid})
        try:
            data = resp.json()
        except Exception:
            print("❌ 轮询响应非 JSON")
            break

        st = data.get("data", {}).get("generateStatus")
        pct = data.get("data", {}).get("percentCompleted")
        print("\n[Status] generateStatus =", st, "percentCompleted =", pct)

        if st == 5:
            imgs = data.get("data", {}).get("images", [])
            if imgs:
                print("✅ 生成完成，图片直链：")
                for i, img in enumerate(imgs, 1):
                    print(f"  [{i}] {img.get('imageUrl')}")

                # === 追加：输出结构化 JSON，方便 server.py 精准获取最终图 ===
                try:
                    final_url = imgs[-1].get("imageUrl") or imgs[0].get("imageUrl")
                    if final_url:
                        print(json.dumps({"result_url": final_url}, ensure_ascii=False))
                except Exception as e:
                    print("⚠️ 输出 JSON 失败：", e)
            else:
                print("⚠️ 完成但未返回 images，请到 Liblib 控制台核对。")
            # 结束轮询循环（注意：这个 break 必须在 while True 之内）
            break

        if isinstance(st, int) and st < 0:
            print("❌ 生成失败: ", json.dumps(data, ensure_ascii=False, indent=2))
            break

        time.sleep(2)


# =========================================================
#                        入口
# =========================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-url", help="输入图的公网 URL（优先于 ENV/本地）")
    parser.add_argument("--emotion",  help="情绪标签：1=严肃,2=高兴,3=惊讶,4=皱眉")
    args = parser.parse_args()

    # 动态决策：输入图 URL + 情绪
    print(f"\n[DEBUG] CLI args.image_url: {args.image_url}")
    image_url = resolve_input_image_url(args.image_url)
    emotion_id = (args.emotion or os.getenv("LIBLIB_EMOTION") or DEFAULT_EMOTION_ID).strip()

    print("\n=== 提交生成任务 ===")
    print("  image_url :", image_url)
    print("  emotion   :", emotion_id)

    gen_id = submit_job(image_url, emotion_id=emotion_id)
    if gen_id:
        print("\n任务 UUID:", gen_id)
        print("\n=== 轮询任务状态 ===")
        poll_status(gen_id)
    else:
        print("\n❌ 提交失败（若 code=401：检查签名四参数是否在 query；"
              "URI 是否 EXACT='/api/generate/comfyui/app'；时间戳毫秒；密钥/工作流授权一致）")








