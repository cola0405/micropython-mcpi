# micropython-mcpi

把 [mcpi](https://github.com/martinohanlon/mcpi)（Minecraft: Pi edition API Python 库）
移植到 **MicroPython**，目标平台 **ESP32**，对端是 **Java 版 Minecraft + RaspberryJuice 插件**。

**方案 A：最小侵入移植** —— 只改 `mcpi/connection.py` 和 `mcpi/util.py` 两个文件，
其余 6 个文件与上游逐字一致。**对外 API 一个字没变**，现成的 mcpi 教学代码可以原样搬到板子上跑。

```
from mcpi.minecraft import Minecraft
from mcpi import block

mc = Minecraft.create("192.168.1.100", 4711)
mc.setBlock(0, 80, 0, block.STONE.id)
```

---

## 目录结构

```
mcpi/                     库本体（与上游同名同布局）
  connection.py           ← 重写：收发层，MicroPython 兼容
  util.py                 ← 重写：参数展开与序列化
  minecraft.py            未改动（Minecraft / player / entity / camera / events）
  block.py                未改动（108 个方块常量）
  entity.py               未改动（79 个实体常量，按需求保留）
  event.py  vec3.py  __init__.py    未改动
examples/esp32_hello.py   ESP32 完整示例（WiFi + 放方块 + 事件轮询）
tests/
  test_transport_cpython.py   端到端测试（17 个用例，自带 mock 服务端，不需要 MC 服务端）
  test_mp_compat.py           兼容性自检 69 项断言，**CPython 和板上都能跑**
  mock_server.py              模拟 RaspberryJuice 的本地测试服务端
tools/wasm_micropython/  在没有硬件的情况下，用 MicroPython 的 wasm 构建做冒烟验证
package.json              给 mip / mpremote mip 用的包清单（声明要装哪 8 个 .py）
docs/                    移植评估报告（含逐条风险分析与方案对比）
```

---

## 为什么需要改？—— 四处 CPython 专属写法

| 位置 | 上游写法 | MicroPython 的问题 | 改法 |
|---|---|---|---|
| `util.py:1` | `import collections.abc` + `isinstance(e, collections.Iterable)` | MicroPython 的 `collections` 只有 `deque`/`namedtuple`/`OrderedDict`，**没有 abc 子模块** → `AttributeError`。而 `flatten()` 是所有 `send()` 的必经路径，等于全库瘫痪 | 标量白名单 + `iter()` 探测 + `except TypeError` |
| `util.py:22` | `str(m).encode("UTF-8")` | 只支持 `'utf-8'`/`'utf8'`/`'ascii'`，**大写变体不被接受**；新版固件会校验编码名并抛 `LookupError`（老固件静默按 utf8 处理，所以这是"换块新板子就崩"的隐形雷） | `.encode()`（默认 utf-8） |
| `connection.py:54` | `socket.makefile("r").readline()` | 文档明确 **只支持二进制模式** `'rb'/'wb'/'rwb'` | 自建带缓冲的 `readline` |
| `connection.py:23` | `select.select([sock], [], [], 0.0)` | 文档：**"仅部分 port 提供"**，推荐 `poll()` | 优先 `select.poll()`，退化到 `select`，都没有则保守返回 |
| `connection.py:29` | `sys.stderr.write(...)` | 多数裸机 port 没有 `sys.stderr` | `Connection.debug` 开关 + `print()` |

### 顺手修掉的两个上游 bug

1. **丢响应**（真实缺陷，已在 CPython 上复现）：上游每次 `receive()` 都新建一个
   `makefile("r")`，其缓冲预读进来的后续字节会随这个临时对象一起被丢弃。
   服务端一次发两行时，第二行永久丢失。
   本版用持久缓冲，多余的字节留在 `self._buf` 里等下一次 `receive()` ——
   `tests/test_transport_cpython.py::t06` 就是这条回归用例。
2. **`bytes` 被拆散**：上游判据是 `isinstance(e, Iterable) and not isinstance(e, str)`，
   而 `bytes` 是 Iterable 又不是 str，于是 `b"abc"` 会被拆成 `97,98,99` 发出去。
   本版把 `bytes`/`bytearray` 当标量处理。

### 新增的可选能力（都是向后兼容的增量，默认行为与上游一致）

| 能力 | 说明 |
|---|---|
| `Connection(host, port, timeout=5)` | 读超时。板子上的 WiFi 会抖，不设超时可能永久卡死。`Minecraft.create()` 仍只有两个参数 |
| `Connection(host, port, max_line=N)` | 单行响应上限（默认 16384 字节）。超限抛 `ResponseTooLarge`，它是 `RequestError` 的子类，所以上游的 `except RequestError` 依然有效。**大范围 `getBlocks()` 在 MCU 上会把堆打爆**，这个限制把它变成一条清晰的报错 |
| `Connection.close()` | 显式释放 socket |
| `Connection.debug = True` | 打开后打印 `drain()` 丢掉了多少字节 |

---

## 快速开始（ESP32）

### 方式 1：用 `mip` 安装（推荐）

```bash
# 装库到板子的 /lib（mpremote 会自动找到 sys.path 里以 /lib 结尾的目录）
mpremote connect COM3 mip install github:cola0405/micropython-mcpi

# 装完确认一下
mpremote connect COM3 exec "import mcpi.minecraft; print('ok')"
```

几点说明：

- **下载是在你的电脑上完成的**（`mpremote mip` 用 PC 的 `urllib` 拉文件，再通过串口写进板子），
  所以**板子不需要联网**就能装。注意区分：如果你是在板子的 REPL 里跑 `import mip; mip.install(...)`，
  那就是板子自己联网下载了。
- 想锁定版本：`mpremote connect COM3 mip install github:cola0405/micropython-mcpi@v1.2.1-mp1`
- 装到别的目录：`mpremote connect COM3 mip install --target /flash/lib github:cola0405/micropython-mcpi`
  （该目录必须在 `sys.path` 里才能 import）
- 不走网络、直接从本地克隆装：`mpremote connect COM3 mip install ./package.json`
- `package.json` 里的文件清单就是 `mcpi/` 下那 8 个 `.py`。

### 方式 2：手动拷贝（不用 mip）

```bash
mpremote connect COM3 fs cp -r mcpi :mcpi
```

### 装示例并运行

```bash
# 示例改名为 main.py 后上电自启（记得先改 WIFI_SSID / WIFI_PASS / MC_HOST）
mpremote connect COM3 fs cp examples/esp32_hello.py :main.py

# 建议先跑一次兼容性自检，确认固件没问题
mpremote connect COM3 fs cp tests/test_mp_compat.py :
mpremote connect COM3 run test_mp_compat.py

# 复位运行
mpremote connect COM3 reset
```

`examples/esp32_hello.py` 里要改三处：WiFi 名、WiFi 密码、`MC_HOST`（跑 Minecraft 服务端的电脑 IP）。

### 服务端准备（Java 版 + RaspberryJuice）

1. 把 [RaspberryJuice](https://github.com/zhuowei/RaspberryJuice) 的 jar 放进服务端 `plugins/`，重启。
2. 监听端口默认 **4711**；板子和服务器要在同一局域网。
3. ⚠️ **坐标基准**：RaspberryJuice 默认是**相对出生点的相对坐标**，`setBlock(0,80,0)` 不会落在你以为的地方。首次启动后会生成配置文件，把坐标模式改成绝对坐标再重启。
4. 方块 id 用的是 Minecraft Pi Edition 的旧数字 id，RaspberryJuice 负责映射；很新的 MC 版本上个别 id 可能对不上。

### 省内存（可选，方案 C）

`mpy-cross` 预编译成 `.mpy` 再冻结进固件，库就不占 Python 堆：

```bash
mpy-cross mcpi/*.py            # 生成 .mpy
# 然后在固件的 manifest.py 里：
#   freeze("mcpi", ("connection.mpy", "minecraft.mpy", ...))
```

---

## 板上的两个注意点

- **内存**：`block.py` + `entity.py` 共 187 个模块级常量对象，实测量级在几十 KB 的 Python 堆里
  （wasm 版 MicroPython 1.17 实测：导入整个包后 `gc.mem_free()` 75 KB，共 3 MB 堆）。
  ESP32 余量充足；如果你以后要压到 ESP8266，先把 `entity.py` 换成裸 int。
- **浮点**：多数 MCU port 是**单精度** float（约 7 位有效数字），`player.getPos()` 回读会有细微差异。
  方块坐标是整数，不受影响。
- **别去掉 `drain()`**：见下一节。

---

## 协议行为（照着 RaspberryJuice 的真实实现，不是本库的限制）

读源码可知（`RemoteSession.java`）：

- **set 类命令完全静默**：`world.setBlock`/`setBlocks`/`setSign`/`chat.post`/`player.set*`/`events.clear`
  执行完**什么都不回**，源码里根本没有 `send("OK")`，连接时也没有欢迎语。
  → 所以 `send()` 之后**不要**去 `receive()`，否则会读走下一条命令的响应。
- **只有查询类命令有响应**：`get*`/`poll*`/`remove*`/`spawnEntity`。
- **出错或不支持的命令回 `Fail`** → 客户端抛 `RequestError`。
- `camera.*`、`world.checkpoint.*` 是 Pi Edition 专有命令，RaspberryJuice **不支持** → 回 `Fail`。
  因为它们没人读，这个 `Fail` 会留在缓冲区里污染下一条读命令 —— 这正是 `drain()`
  存在的意义（每次发送前清掉脏响应）。`tests/test_transport_cpython.py::t17` 专门验证了
  "有 drain 就没事、关掉 drain 就被污染"。

---

## 验证情况

**CPython 端到端（17/17 通过）** —— `tests/test_transport_cpython.py`
自带 mock RaspberryJuice 服务端，不依赖真的 Minecraft。覆盖：请求字节精确格式（含 UTF-8 中文）、
世界/玩家/实体/事件四类 API 全链路、`Fail → RequestError`、超长响应、多行响应回归、
UTF-8 非法字节降级、`max_line` 保护、超时与关闭、公开 API 表面、AST 静态守卫。

**MicroPython 语言层（69 项断言）** —— `tests/test_mp_compat.py`
在 CPython 上全绿；同时已在**真实的 MicroPython 1.17 解释器**（wasm 构建）上跑通，
实测确认：`collections.abc` 不存在、`select.poll` 存在、`math` 存在、`bytearray` 可用于
`isinstance` 元组、`bytes.find/endswith`、`chr`、`str.encode("utf-8")` 均可用，
整包可导入且相对 import 链路正常。

> 复现方式见 `tools/wasm_micropython/README.md`（在没有硬件时用 wasm 版 MicroPython 做冒烟验证）。
> 剩下的真机确认只需一条命令：`mpremote run test_mp_compat.py`。

**上游 bug 复现证据**：用上游原版 `connection.py` 跑 `t06` 的同一场景，
第二行 `receive()` 2 秒超时失败（数据被丢），本版返回 `'SECOND'`。

---

## 后续可做（本次未做）

- 断线自动重连 + 心跳（目前 `Connection` 裸连无重试，WiFi 抖了就抛 OSError）
- 方案 B：单文件精简版 `mcpi_lite.py`（丢 camera/events/entity 常量表，ESP8266 友好）
- 硬件映射：按钮 / 摇杆 / OLED / IMU → `setBlock`，这才是用板子的价值所在
- 用 `uasyncio` 或 `micropython.schedule()` 把阻塞收发挪出主循环

---

## 许可

本移植沿用上游 **MIT**（见 `LICENSE`），并保留 Minecraft: Pi edition 的原始许可
（`minecraft-pi-edition-LICENSE.txt`）与原作者署名（Martin O'Hanlon / Aron Nieminen, Mojang AB）。
