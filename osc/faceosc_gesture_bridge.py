from pythonosc import dispatcher, osc_server, udp_client

# Wekinator 接收端
wek_host = "127.0.0.1"
wek_port = 6448
client = udp_client.SimpleUDPClient(wek_host, wek_port)

# 存储 gesture 数值
gesture_values = {
    "/gesture/mouth/width": 0.0,
    "/gesture/mouth/height": 0.0,
    "/gesture/eye/left": 0.0,
    "/gesture/eye/right": 0.0,
    "/gesture/eyebrow/left": 0.0,
    "/gesture/eyebrow/right": 0.0,
}

def gesture_handler(address, *args):
    if address in gesture_values:
        gesture_values[address] = args[0]

    # 拼成固定长度数组发给 Wekinator
    values = list(gesture_values.values())
    print(f"Forwarding gestures: {values}")
    client.send_message("/wek/inputs", values)

if __name__ == "__main__":
    print("Listening FaceOSC gestures → forwarding 6 inputs to /wek/inputs")
    disp = dispatcher.Dispatcher()
    for addr in gesture_values.keys():
        disp.map(addr, gesture_handler)

    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 6448), disp)
    print(f"Serving on {server.server_address}")
    server.serve_forever()
