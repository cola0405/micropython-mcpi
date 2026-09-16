"""
最小 setBlock 示例：连 WiFi → 连 Minecraft → 在玩家身边放方块 → 回读确认。

安装与运行（PC 上先 `pip install mpremote`）：

    mpremote connect COM3 mip install github:cola0405/micropython-mcpi
    mpremote connect COM3 fs cp examples/setblock_quickstart.py :main.py
    mpremote connect COM3 reset

跑之前改下面三个常量：WiFi 名 / WiFi 密码 / MC_HOST（跑 Minecraft 服务端的电脑 IP）。

为什么用「玩家位置 + 偏移」而不是绝对坐标？
    RaspberryJuice 默认把坐标解释成**相对出生点**的相对坐标，直接写
    mc.setBlock(0, 80, 0, ...) 大概率放不到你看得见的地方。
    以 mc.player.getTilePos() 为基准放，无论服务器怎么配都能落在你眼前。
"""

import time

import network

from mcpi import block
from mcpi.connection import Connection
from mcpi.minecraft import Minecraft

# ---------------------------------------------------------------- 改这三行
WIFI_SSID = "你的 WiFi 名"
WIFI_PASS = "你的 WiFi 密码"
MC_HOST = "192.168.1.100"        # 跑 Minecraft 服务端的电脑 IP（别写 localhost）
MC_PORT = 4711
# ------------------------------------------------------------------------

# 1) 连 WiFi（ESP32 上 WiFi 抖动是常态，所以带重试）
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
if not wlan.isconnected():
    print("正在连 WiFi:", WIFI_SSID)
    wlan.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(40):                      # 最多等 20 秒
        if wlan.isconnected():
            break
        time.sleep(0.5)
if not wlan.isconnected():
    raise OSError("WiFi 连不上，先确认 SSID/密码")
print("WiFi OK, 本机 IP:", wlan.ifconfig()[0])

# 2) 连 Minecraft。
#    这里用 Connection(..., timeout=5) 是为了 IP 填错时 5 秒内报错，而不是一直挂着；
#    想跟上游 mcpi 完全一致就换成 mc = Minecraft.create(MC_HOST, MC_PORT)
mc = Minecraft(Connection(MC_HOST, MC_PORT, timeout=5))
print("已连上 Minecraft:", MC_HOST, MC_PORT)

mc.postToChat("[ESP32] 我来了")             # set 类命令，服务端不回响应

# 3) 先读一下玩家位置（顺便验证「读命令」这条路是通的）
p = mc.player.getTilePos()
print("玩家所在方块坐标:", p.x, p.y, p.z)

# 4) setBlock 的三种写法
# 4.1 只传方块对象：Block 是可迭代的，会自动展开成 id + data
mc.setBlock(p.x + 1, p.y, p.z, block.GOLD_BLOCK)
print("→ 脚边放了金块 @", p.x + 1, p.y, p.z)

# 4.2 传 id + data（data 是「子类型」，羊毛 0~15 是 16 种颜色）
for i in range(5):
    mc.setBlock(p.x + 2, p.y + i, p.z, block.WOOL.id, i)
print("→ 放了 5 格高的彩色羊毛柱")

# 4.3 setBlocks：一次填一个长方体 (x0,y0,z0, x1,y1,z1, id)
mc.setBlocks(p.x + 3, p.y, p.z, p.x + 5, p.y + 2, p.z + 2, block.STONE.id)
print("→ 填了一个 3x3x3 的石块方块")

# 5) 回读验证（这一步证明方块真的落在地图里了，不是只发出去没人管）
got = mc.getBlock(p.x + 1, p.y, p.z)
print("回读刚放的金块:", got, "(应为 41 = GOLD_BLOCK)")

mc.postToChat("[ESP32] 放完了")
print("完成。转身看看脚边 :)")
