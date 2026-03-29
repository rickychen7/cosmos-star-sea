# 🌌 共济星海 · Cosmos Star Sea

一个融合 **人脸情绪识别** 与 **AI 图像生成** 的互动艺术装置。用户在画布上绘制简笔画，系统通过摄像头实时捕捉面部表情，识别情绪后驱动 AI（Qwen 模型 + LibLib ComfyUI 工作流）将简笔画转化为对应情绪风格的 **三渲二科幻城市图**，最终通过二维码分享给用户。

> **Sketch → Emotion → AI Art → QR Download**

![Python](https://img.shields.io/badge/Python-3.9+-blue?logo=python)
![Flask](https://img.shields.io/badge/Flask-2.3-green?logo=flask)
![License](https://img.shields.io/badge/License-MIT-yellow)

## 🎬 演示视频

https://github.com/rickychen7/cosmos-star-sea/raw/main/examples/demo.mp4

---

## ✨ 核心功能

1. **简笔画绘制** — 前端 Canvas 画板，支持画笔/橡皮/清除，实时预览
2. **人脸情绪识别** — FaceOSC 采集面部特征 → Wekinator 机器学习分类为 4 种情绪
3. **AI 图像生成** — 简笔画 + 情绪标签 → LibLib.ai ComfyUI 工作流（Qwen 模型）→ 三渲二风格科幻城市
4. **扫码下载** — 生成的图片通过 ngrok 公网暴露，前端自动生成二维码供扫码下载

## 🎭 情绪映射

| 标签 | 情绪   | 画面风格                         |
|------|--------|----------------------------------|
| 1    | 严肃   | 压迫感、荒凉冷冽、星际堡垒       |
| 2    | 高兴   | 明亮活力、金色蓝绿、未来乌托邦   |
| 3    | 惊讶   | 震撼奇迹、紫蓝银白、光影对比强烈 |
| 4    | 皱眉   | 困惑不安、灰蓝暗橙、隐患危机感   |

---

## 📁 项目结构

```
cosmos-star-sea/
├── frontend/                  # 前端界面
│   ├── canvas_ui.html         # 主界面（画板 + 情绪显示 + 生成结果 + 二维码）
│   ├── canvas_ui2.html        # 早期原型
│   ├── icons/                 # UI 图标素材
│   └── assets/                # 音频等资源
│       └── audio/
├── backend/                   # 后端服务
│   ├── server.py              # Flask 主服务（上传/生成/情绪接口）
│   ├── liblib_test.py         # LibLib ComfyUI API 调用（签名/提交/轮询）
│   └── test_upload.py         # 早期上传服务原型
├── osc/                       # FaceOSC ↔ Wekinator 桥接
│   ├── faceosc_gesture_bridge.py           # FaceOSC → Wekinator (端口 6448)
│   ├── faceosc_gesture_bridge_8338_to_6448.py  # FaceOSC (8338) → Wekinator (6448)
│   ├── faceosc_to_wekinator.py             # 简化版桥接
│   └── wek_output_listener.py              # Wekinator 输出 → latest_emotion.txt
├── examples/                  # 示例图片
│   ├── sketches/              # 用户简笔画示例
│   └── generated/             # AI 生成结果示例
├── requirements.txt
└── README.md
```

---

## 🏗 系统架构

```
┌─────────────┐    OSC     ┌─────────────┐   OSC    ┌─────────────────┐
│   FaceOSC   │ ────────→  │  OSC Bridge │ ──────→  │   Wekinator     │
│  (摄像头)    │  面部特征   │  (Python)   │  6 inputs │  (ML 分类器)    │
└─────────────┘            └─────────────┘          └────────┬────────┘
                                                             │ /wek/outputs
                                                             ▼
┌─────────────┐   HTTP     ┌─────────────┐          ┌─────────────────┐
│  Canvas UI  │ ◄────────→ │  server.py  │ ◄──read──│ wek_output      │
│  (浏览器)    │  画布上传   │  (Flask)    │  emotion │ _listener.py    │
└──────┬──────┘  生成请求   └──────┬──────┘          └─────────────────┘
       │                          │                     写 latest_emotion.txt
       │ 二维码下载                │ 调用 liblib_test.py
       ▼                          ▼
  ┌──────────┐           ┌──────────────────┐
  │ 手机扫码  │           │ LibLib.ai Cloud  │
  │ 下载图片  │           │ (ComfyUI + Qwen) │
  └──────────┘           └──────────────────┘
```

---

## 🚀 快速开始

### 前置条件

- Python 3.9+
- [FaceOSC](https://github.com/kylemcdonald/ofxFaceTracker/releases) (macOS)
- [Wekinator](http://www.wekinator.org/) (机器学习分类)
- [ngrok](https://ngrok.com/) (内网穿透)
- [LibLib.ai](https://www.liblibai.com/) API 密钥

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置密钥

编辑 `backend/liblib_test.py`，替换 API 密钥：
```python
ACCESS_KEY = "YOUR_ACCESS_KEY"       # 从 LibLib.ai 控制台获取
SECRET_KEY = "YOUR_SECRET_KEY"
```

### 3. 启动 ngrok

```bash
ngrok http 5001
```

将获得的公网 URL 更新到 `backend/server.py` 中的 `PUBLIC_URL`。

### 4. 启动各组件

依次在不同终端启动：

```bash
# 终端 1: OSC 桥接（FaceOSC → Wekinator）
python osc/faceosc_gesture_bridge_8338_to_6448.py

# 终端 2: Wekinator 输出监听
python osc/wek_output_listener.py

# 终端 3: Flask 后端服务
python backend/server.py

# 终端 4: 打开前端页面
open frontend/canvas_ui.html
```

### 5. 使用流程

1. 打开 FaceOSC 和 Wekinator，完成表情训练
2. 在前端 Canvas 上绘制简笔画
3. 点击"上传"将画作发送到服务器
4. 系统自动读取当前情绪标签，结合简笔画调用 AI 生成
5. 等待约 1-2 分钟，生成的科幻城市图将显示在右侧面板
6. 扫描二维码即可下载到手机

---

## 🔧 API 接口

| 路由               | 方法 | 说明                          |
|--------------------|------|-------------------------------|
| `/upload`          | POST | 上传画布图片                  |
| `/generate`        | POST | 触发 AI 生成（传入 emotion）  |
| `/get_emotion`     | GET  | 获取当前实时情绪标签          |
| `/events/latest`   | GET  | 获取最近一次生成事件          |
| `/generated/<name>`| GET  | 访问生成的图片                |
| `/uploads/<name>`  | GET  | 访问上传的图片                |
| `/health`          | GET  | 健康检查                      |

---

## 🛠 技术栈

- **前端**: 原生 HTML/CSS/JS + Canvas API + QRCode.js
- **后端**: Python Flask + Requests
- **AI 生成**: LibLib.ai ComfyUI 工作流 + Qwen 图像编辑模型
- **情绪识别**: FaceOSC + Wekinator + python-osc
- **网络**: ngrok 内网穿透

---

## 📄 License

MIT License — 自由使用与修改。
