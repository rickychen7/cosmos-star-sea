from pythonosc import dispatcher, osc_server, udp_client

# Wekinator 接收端设置
wek_host = "127.0.0.1"   # 本地
wek_port = 6448          # Wekinator 默认输入端口
client = udp_client.SimpleUDPClient(wek_host, wek_port)

def forward_handler(address, *args):
    # 收到 FaceOSC 的任何消息后，转发给 Wekinator
    values = list(args)
    print(f"Forwarding {address}: {values}")  # 终端打印调试
    client.send_message("/wek/inputs", values)

if __name__ == "__main__":
    print("Listening on FaceOSC (port 6448), forwarding to Wekinator (/wek/inputs)...")
    disp = dispatcher.Dispatcher()
    disp.map("/*", forward_handler)  # 捕捉 FaceOSC 的所有消息

    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 6448), disp)
    print(f"Serving on {server.server_address}")
    server.serve_forever()
