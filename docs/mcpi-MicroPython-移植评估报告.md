# mcpi → MicroPython 移植可行性评估

> 评估对象：`D:\project\github\mcpi-master\mcpi-master`（mcpi v1.2.1，Martin O'Hanlon，MIT）
> 评估方式：全量源码审阅 + MicroPython 官方文档逐项比对（socket / select / builtins / collections / genrst）
> 结论日期：2026-09-16　　本文只做评估，未改动任何代码

---

## 一、结论

**可以移植，而且属于低难度移植。**

mcpi 本质是一个**同步阻塞的 TCP 文本协议薄封装**：没有 C 扩展、没有线程、没有 asyncio、没有第三方依赖、没有 f-string / 类型注解 / dataclass，全部逻辑就是「拼一行字符串 → 发出去 → 读一行回来」。这恰好是 MicroPython 最擅长的形态（MCU 上没有多任务，同步阻塞反而最简单）。

需要改动的只有 **4 处硬阻塞 + 2 处建议改动**，量级在 **40~60 行**，且**不需要改变对外 API 形状** —— 也就是说，所有现成的 Minecraft 教学代码（`from mcpi import minecraft` / `mc.setBlock(...)`）可以原样搬到板子上跑。

真正需要你拍板的不是「能不能」，而是「目标板子是什么、要不要砍 entity 常量表、要不要加硬件外设」——见第六节。

---

## 二、代码解剖（全库 8 个文件，约 650 行）

| 文件 | 行数 | 职责 | 移植风险 |
|---|---|---|---|
| `connection.py` | 63 | TCP 连接 + 行协议收发 | **高（3 处阻塞）** |
| `util.py` | 23 | `flatten` 参数展开 + 序列化 | **高（2 处阻塞）** |
| `minecraft.py` | 382 | 主门面 + Player/Entity/Camera/Events | 低 |
| `block.py` | 134 | 方块 ID 常量表（~100 个） | 低（内存） |
| `entity.py` | 102 | 实体 ID 常量表（77 个） | 低（内存） |
| `vec3.py` | 115 | 三维向量 | 无 |
| `event.py` | 68 | 事件对象 | 无 |
| `__init__.py` | 空 | 包声明 | 无 |

协议：`命令.子命令(参数,参数,...)\n`，服务器回 `OK` / `Fail` / 具体数据行。

---

## 三、阻塞点清单（逐条带定位）

### 🔴 P0-1　`util.py:1-4` — `collections.abc` 不存在

```python
try:
    import collections.abc as collections
except ImportError:
    import collections as collections          # ← MicroPython 走这条
...
if isinstance(e, collections.Iterable) ...     # ← AttributeError
```

MicroPython 的 `collections` 只有 `deque` / `namedtuple` / `OrderedDict`，**没有 `abc` 子模块，也没有 `Iterable`**。`import collections.abc` 抛 `ImportError` → 落到 fallback → 调用 `collections.Iterable` 时抛 `AttributeError`。

**影响面最大**：`flatten()` 是所有 `send()` 的必经路径，等于全库瘫痪。
**改法**：改用明确的类型白名单判据（`isinstance(e, (list, tuple, Vec3, Block, Entity))` 或 `hasattr(e, "__iter__") and not isinstance(e, (str, bytes))`）。

### 🔴 P0-2　`util.py:22` — 大写编码名会被新版 MicroPython 拒绝

```python
return str(m).encode("UTF-8")      # ← 注意是大写 "UTF-8"
```

MicroPython 官方文档（`genrst/builtin_types.html`）原文：

> `str.encode()` constructor only supports encoding arguments **'utf8', 'utf-8' and 'ascii'**. Other encodings like 'latin-1' are not supported. **Other string forms such as 'UTF8' are not supported.**

并且新版固件的 release notes 明确写了行为变更：

> MicroPython now **validates** the encoding argument to `str.encode()` and `bytes.decode()` … In particular, "latin-1" is not supported, and neither are **uppercase variants like "UTF8"**. The previous behaviour for an unsupported encoding was to silently pass through the data using "utf8" encoding, but MicroPython will now **raise a LookupError**.

**结论**：`"UTF-8"` 是定时炸弹 —— 老固件「碰巧能跑」，新固件（约 v1.24+）直接 `LookupError`。
**改法**：`.encode()`（默认 utf-8）或 `.encode("utf-8")`。

### 🔴 P0-3　`connection.py:54` — `makefile("r")` 文本模式不受支持

```python
s = self.socket.makefile("r").readline().rstrip("\n")
```

MicroPython 文档原文：`socket.makefile(mode='rb', buffering=0)` —— "The support is **limited to binary modes only** ('rb', 'wb', and 'rwb')。CPython's arguments: encoding, errors and newline are **not supported**"，且 "values of buffering parameter is **ignored and treated as if it was 0**"。

**改法（二选一）**：
- 保守：`makefile("rb")` + `.rstrip(b"\n").decode()`（注意再次踩 P0-2 的编码坑）
- 推荐：**自己实现 readline**，用 `recv(1)` 循环或带缓冲的 `recv` 累积到 `\n` 为止。既绕开 makefile 的模式限制，又顺带修掉下面的隐藏 bug。

**⚠️ 顺带发现一个 CPython 下也存在的真实 bug**：这里**每次调用 receive 都新建一个 file 对象**。CPython 的 makefile 是带缓冲的，`readline()` 会预读超过一行的数据，而这些预读内容随这个临时 file 对象一起被丢弃 —— 在 `getBlocks()` / `getEntities()` / 连发多条命令的场景下会**丢响应**。移植时顺手修掉。

### 🟠 P1-4　`connection.py:23` — `select.select()` 只有部分 port 提供

```python
readable, _, _ = select.select([self.socket], [], [], 0.0)
```

MicroPython 文档原文：`select.select(rlist, wlist, xlist[, timeout])` —— "This function is provided by **some** MicroPython ports for compatibility and is **not efficient**. Usage of `Poll` is recommended instead."

即：**不保证存在**（`select.poll()` 才是官方推荐、跨 port 一致的 API）。
**改法**：`poll = select.poll(); poll.register(sock, select.POLLIN); poll.poll(0)`；或者干脆 `sock.setblocking(False)` + `try: recv() except OSError`。
**说明**：`drain()` 本身是 Python 2 时代的清缓冲 hack，MCU 上每次 send 都多跑一次 select 是纯浪费，删掉也不影响功能。

### 🟠 P1-5　`connection.py:29` — `sys.stderr` 多数裸机 port 没有

```python
sys.stderr.write(e)
```

只用在 `drain()` 的调试输出里。裸机端口的 `sys` 通常不提供 stdio 文件对象（或没有 `write`）。
**改法**：删掉，或换成 `print()`。

### 🟡 P2-6　其余可接受 / 需注意项

| 项 | 位置 | 现状与对策 |
|---|---|---|
| `math.floor` | `minecraft.py:36` | MicroPython 有 math；但**无 float 的极简 build** 不可用。风险很低。 |
| 域名解析 | `Minecraft.create(address, port)` | MicroPython 文档要求优先 `getaddrinfo()`，部分 port 只接受数字 IP 元组 → 板上填局域网 IP 即可。 |
| `sendall()` | `connection.py:50` | 存在，但官方建议用 `write()`（非阻塞语义更明确）。低风险。 |
| 单精度浮点 | `vec3` / `getPos` | ESP32 等 port 默认 **single precision float**（~7 位有效数字），`getPos()` 回读会比 CPython 有细微差异，方块坐标（int）不受影响。 |
| 内存占用 | `block.py` + `entity.py` | 约 180 个模块级实例对象，估算 **15~25 KB RAM**。ESP32（空闲堆 100KB+）无压力，**ESP8266 偏紧**。 |
| `setup.py` / pip | 根目录 | 板上不走 pip。部署方式改为：复制目录 / `mpremote` 上传 / `mpy-cross` 预编译 + manifest 冻结进固件。 |
| 包结构 | `mcpi/` 目录 | MicroPython 支持带 `__init__.py` 的包目录，无需改动。 |

### ✅ 确认无风险的部分

无 `threading` / `asyncio` / `multiprocessing` / `signal` / `ctypes` / `typing`；无 f-string；`bytes.join`、`%` 格式化、类继承、`staticmethod`、生成器、`map`、列表推导、`hash`/`__eq__` —— MicroPython 全部支持。事件是**拉取式**（`pollBlockHits()` 主动问），没有回调/中断需求，天然适配 MCU 主循环。

### 🔧 建议顺手修的既有 bug（与移植无关，但都在改动路径上）

1. `makefile` 每次新建导致**预读数据丢失**（P0-3 已述）。
2. `util.flatten` 会把 **bytes 拆成一堆 int**：`isinstance(e, Iterable) and not isinstance(e, str)` —— bytes 是 Iterable 且不是 str，判定成立。改成白名单类型最稳。
3. `Vec3.__eq__` / `Block.__eq__` 不判 `rhs` 类型，与 `int`/`None` 比较直接 `AttributeError`。
4. `event.py:67` `ProjectileEvent.Hit()` 里传的是 `BlockEvent.HIT`（两者值都是 0，巧合掩盖了错误）。
5. `__cmp__` 是 Python 2 遗留，Py3 / MicroPython 都**不会调用** —— 排序比较静默失效。

---

## 四、三个移植方案（供选择）

### 方案 A：最小侵入移植（推荐）
保留 `mcpi/` 包结构，只重写 `connection.py` 的收发与 `util.py` 的参数序列化，其余 6 个文件**一字不改**。
- 改动量：约 40~60 行
- 优点：对外 API 100% 兼容，现成教学代码零改动；改动同时修掉上游 bug
- 代价：`block.py` + `entity.py` 的常量表 RAM 占用照旧

### 方案 B：单文件精简版 `mcpi_lite.py`
把 `Minecraft` + `Block` + `Vec3` + 收发逻辑压成一个文件（约 200 行），丢掉 camera / events / entity 常量表。
- 优点：flash 与 RAM 占用最小，`import` 最快，**ESP8266 友好**
- 代价：API 是子集，教学代码里用到 entity / event 的部分要删

### 方案 C：冻结进固件
在 A 或 B 之上，用 `mpy-cross` 编译成 `.mpy`，再通过 `manifest.py` 冻结进 ROM。
- 优点：库常驻 flash 不占 RAM 堆，启动即用
- 代价：需要自己编译固件，迭代时要重新刷机（开发阶段可先用 A/B，定型后再冻结）

### 可选增强（MCU 才有的价值）
- 断线重连 + 心跳（WiFi 抖动是常态，`connection` 目前裸连无重试）
- `micropython.schedule()` 或 `uasyncio` 包装，把阻塞收发挪出主循环
- 硬件映射：按钮 / 摇杆 / OLED / IMU → `setBlock`，这才是用板子而不是用树莓派的理由
- `getPos` 轮询节流 + 响应缓存，减少 MCU 上的 syscall 次数

---

## 五、部署与验证建议

1. **先在 CPython 上验证改动**：由于改动本身修的是 `makefile` 丢数据这类真实 bug，先在 PC 上对着 RaspberryJuice 跑通，再上板，能省掉大量串口调试时间（用 `mpremote` 或 Thonny 传文件）。
2. **最小冒烟序列**（覆盖 3 个关键路径）：
   - `mc.postToChat("hi")` → 验证 send + 编码链（P0-2）
   - `mc.getBlock(0,0,0)` → 验证 sendReceive + 短响应读取（P0-3）
   - `mc.setBlocks(0,0,0, 8,8,8, 1)` → 验证 flatten 展开（P0-1）
   - `mc.getBlocks(...)` → 验证长响应不丢数据（旧 makefile bug）
3. **网络前提**：板子与 Minecraft 服务器（Java 版 + RaspberryJuice 插件，端口 4711）在同一局域网；板上 `WLAN` 连接成功后用数字 IP 连服务器。
4. **许可**：mcpi 为 MIT，另附 Mojang Pi edition 许可。移植版**保留 `LICENSE` 与原作者署名**即可自由分发。

---

## 六、需要你拍板的 5 个问题

1. **目标硬件**是哪个？ESP32 / ESP32-S3 / Pico W / ESP8266 / 其他？
   （决定内存预算：ESP8266 建议直接走方案 B）
2. **目标服务器**是 Minecraft Pi Edition，还是 Java 版 + RaspberryJuice？
   （协议一致，但**扩展命令集不同**，且 RaspberryJuice 有「坐标基准/相对坐标」配置项会直接影响方块落点）
3. **要不要保留 `entity.py`**？77 个常量是内存大头，多数教学场景用不上。
4. **是否要求 API 完全向后兼容**（方案 A），还是允许精简（方案 B）？
5. **除了联网放方块，还要接硬件吗**？（按钮/传感器 → 游戏动作）如果只是想让代码跑在板子上，方案 A 就够了；如果要做出板子的独特价值，需要一起设计外设映射。

---

## 七、总体判断

| 维度 | 评价 |
|---|---|
| 技术可行性 | ✅ 完全可行（无架构级障碍） |
| 改动量 | 小（40~60 行，集中在 2 个文件） |
| API 兼容性 | 可做到 100% 兼容 |
| 主要风险 | 只在于「MicroPython 版本差异」（`select` / `makefile` / `encode` 三处按 port 和版本而异） |
| 建议路径 | 方案 A 先行验证 → 视板子内存决定是否转 B → 定型后 C 冻结 |
| 预估性质 | 不是「能不能做」的问题，是「半天到一天」的工程量 |
