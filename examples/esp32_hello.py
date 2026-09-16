"""
ESP32 上的 mcpi 用法示例（对着 Java 版 + RaspberryJuice 服务端）。

使用前改两处：WIFI 的 SSID/密码，以及 MC_HOST（跑 Minecraft 服务端的电脑 IP）。

上板：
    mpremote connect COM3 fs cp -r mcpi :mcpi
    mpremote connect COM3 fs cp examples/esp32_hello.py :main.py
    mpremote connect COM3 reset

注意（都是 RaspberryJuice 的真实行为，不是本库的限制）：
  * set 类命令（setBlock/setBlocks/postToChat/setPos…）**服务端不回响应**，
    所以调用它们之后不要去 receive，否则会读走别人的响应。
  * 查询类命令（getBlock/getPlayerIds/pollBlockHits…）才有响应。
  * 出错或不支持的命令回 "Fail"。camera.* 和 world.checkpoint.* 是 Pi Edition
    专有命令，RaspberryJuice 不支持 → 会回 Fail；因为它们没人读，会留在缓冲区
    里污染下一条读命令 —— 库里的 drain() 就是干这个的，别把它关掉。
"""

import time

import network

from mcpi import block, entity
from mcpi.connection import Connection
from mcpi.minecraft import Minecraft

WIFI_SSID = "你的 WiFi 名"
WIFI_PASS = "你的 WiFi 密码"
MC_HOST = "192.168.1.100"     # 跑 Minecraft 服务端的电脑 IP，别用 localhost
MC_PORT = 4711


def wifi_connect(timeout=20):
    """连 WiFi。ESP32 上 WiFi 抖动是常态，所以带重试。"""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        print("WiFi 已连接:", wlan.ifconfig()[0])
        return wlan

    print("正在连接 WiFi:", WIFI_SSID)
    wlan.connect(WIFI_SSID, WIFI_PASS)
    deadline = time.time() + timeout
    while not wlan.isconnected():
        if time.time() > deadline:
            raise OSError("WiFi 连接超时")
        time.sleep(0.5)
    print("WiFi 已连接:", wlan.ifconfig()[0])
    return wlan


def main():
    wifi_connect()

    # timeout 是本移植新增的**可选**参数（上游 create() 不支持），给读操作加上限，
    # WiFi 掉线时不会永久卡死。想完全保持上游行为就用：
    #     mc = Minecraft.create(MC_HOST, MC_PORT)
    mc = Minecraft(Connection(MC_HOST, MC_PORT, timeout=5))

    mc.postToChat("[ESP32] 我上线了")
    print("已连上 Minecraft:", MC_HOST, MC_PORT)

    # ---- 1. 放方块：set 类命令，服务端不回响应 ----
    mc.setBlock(0, 80, 0, block.STONE.id)
    mc.setBlocks(-3, 79, -3, 3, 79, 3, block.GOLD_BLOCK.id)
    print("方块放好了")

    # ---- 2. 读方块：查询类命令，有响应 ----
    x, y, z = 0, 80, 0
    print("(0,80,0) 的方块 id =", mc.getBlock(x, y, z))
    pos = mc.player.getPos()
    print("玩家位置: %.1f %.1f %.1f" % (pos.x, pos.y, pos.z))

    # ---- 3. 刷一行羊毛墙，顺便演示 Block 可以带 data ----
    for i in range(8):
        mc.setBlock(5, 80 + i, 5, block.WOOL.id, i)   # 8 种颜色
    print("羊毛墙完成")

    # ---- 4. 生成一只猪 ----
    eid = mc.spawnEntity(0, 81, 0, entity.PIG)
    print("猪的 entityId =", eid)

    # ---- 5. 主循环：轮询玩家用剑打方块的事件 ----
    print("等玩家用剑打方块（Ctrl-C 或 60 秒后退出）…")
    deadline = time.time() + 60
    while time.time() < deadline:
        # 拉取式事件：主动问，不依赖回调，很适合 MCU 主循环
        for hit in mc.player.pollBlockHits():
            print("被打了: (%d,%d,%d) face=%d" % (hit.pos.x, hit.pos.y, hit.pos.z, hit.face))
            # 在他打的位置上方放个石头
            mc.setBlock(hit.pos.x, hit.pos.y + 1, hit.pos.z, block.STONE.id)
        time.sleep(0.2)            # 别把 CPU 和网络跑满

    mc.postToChat("[ESP32] 我下线了")


try:
    main()
except Exception as e:                 # 板子上异常会直接停掉脚本，这里打个日志方便排查
    print("出错了:", type(e).__name__, e)
    raise
