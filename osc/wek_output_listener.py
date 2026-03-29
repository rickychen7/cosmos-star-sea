# -*- coding: utf-8 -*-
"""
wek_output_listener.py - 监听 Wekinator 输出并写入 latest_emotion.txt
监听端口：12000
监听地址：/wek/outputs
输出文件：latest_emotion.txt（内容为 1/2/3/4）
"""

from pythonosc import dispatcher, osc_server
import os
import time

# Wekinator 输出配置
WEKINATOR_HOST = "127.0.0.1"
WEKINATOR_PORT = 12000
OUTPUT_FILE = "latest_emotion.txt"

def emotion_handler(address, *args):
    """
    处理 Wekinator 输出的情绪数据
    期望输入：/wek/outputs 地址，包含一个数值（1-4）
    """
    if len(args) > 0:
        try:
            # 获取第一个参数作为情绪标签
            emotion_value = float(args[0])
            
            # 将浮点数转换为整数（1-4）
            emotion_id = int(round(emotion_value))
            
            # 确保在有效范围内
            if emotion_id < 1:
                emotion_id = 1
            elif emotion_id > 4:
                emotion_id = 4
            
            # 写入文件
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write(str(emotion_id))
            
            print(f"🎭 Wekinator 输出情绪: {emotion_id} (原始值: {emotion_value})")
            
        except (ValueError, TypeError) as e:
            print(f"❌ 解析情绪数据失败: {e}, 原始数据: {args}")
    else:
        print("⚠️ 收到空数据")

def main():
    print(f"🎧 启动 Wekinator 输出监听器...")
    print(f"   监听地址: {WEKINATOR_HOST}:{WEKINATOR_PORT}")
    print(f"   监听路径: /wek/outputs")
    print(f"   输出文件: {OUTPUT_FILE}")
    print(f"   情绪映射: 1=严肃, 2=高兴, 3=惊讶, 4=皱眉")
    print("-" * 50)
    
    # 创建调度器
    disp = dispatcher.Dispatcher()
    disp.map("/wek/outputs", emotion_handler)
    
    # 创建服务器
    server = osc_server.ThreadingOSCUDPServer((WEKINATOR_HOST, WEKINATOR_PORT), disp)
    
    print(f"✅ 监听器已启动，等待 Wekinator 输出...")
    print(f"   服务器地址: {server.server_address}")
    print("-" * 50)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 监听器已停止")
    except Exception as e:
        print(f"❌ 监听器错误: {e}")

if __name__ == "__main__":
    main()