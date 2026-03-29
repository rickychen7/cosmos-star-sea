from pythonosc import dispatcher, osc_server, udp_client

IN_PORT = 8338          # 这里改为监听 8338（FaceOSC 发到 8338）
WEK_HOST = "127.0.0.1"
WEK_PORT = 6448         # Wekinator 默认输入端口

client = udp_client.SimpleUDPClient(WEK_HOST, WEK_PORT)

gesture_values = {
    "/gesture/mouth/width": 0.0,
    "/gesture/mouth/height": 0.0,
    "/gesture/eye/left": 0.0,
    "/gesture/eye/right": 0.0,
    "/gesture/eyebrow/left": 0.0,
    "/gesture/eyebrow/right": 0.0,
}

def gesture_handler(address, *args):
    if address in gesture_values and len(args) > 0:
        gesture_values[address] = float(args[0])
    values = [
        gesture_values["/gesture/mouth/width"],
        gesture_values["/gesture/mouth/height"],
        gesture_values["/gesture/eye/left"],
        gesture_values["/gesture/eye/right"],
        gesture_values["/gesture/eyebrow/left"],
        gesture_values["/gesture/eyebrow/right"],
    ]
    print("Forwarding to /wek/inputs:", values)
    client.send_message("/wek/inputs", values)

if __name__ == "__main__":
    print(f"Listening FaceOSC on {IN_PORT} → forwarding 6 values to {WEK_HOST}:{WEK_PORT} /wek/inputs")
    disp = dispatcher.Dispatcher()
    for addr in gesture_values.keys():
        disp.map(addr, gesture_handler)
    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", IN_PORT), disp)
    print("Server:", server.server_address)
    server.serve_forever()
