"""
MicroPython 兼容性自检（CPython / MicroPython 双跑）。

这个文件刻意不使用 socket / threading / 文件系统 —— 纯语言层与纯逻辑，
所以既能在这台电脑上用 CPython 跑，也能直接丢到 ESP32 上跑：

    # PC 上
    python tests/test_mp_compat.py

    # 板子上（把仓库目录推上去后）
    mpremote connect COM3 fs cp -r mcpi :mcpi
    mpremote connect COM3 fs cp tests/test_mp_compat.py :
    mpremote connect COM3 run test_mp_compat.py

两部分内容：
  A. 环境探针：把目标解释器的关键差异打出来（大写编码名能不能用、
     collections.abc 在不在、socket 有没有 makefile、剩余堆内存……）。
     换固件后可重跑，一眼看出环境是否变了。
  B. 功能断言：flatten 展开、参数序列化、Vec3、方块/实体常量表、事件对象。
     这些是移植改动最集中的地方（util.py），也是最容易在板上翻车的地方。

退出码：CPython 下失败退出 1；MicroPython 下只打印结论（避免裸机 soft reset）。
"""

import sys


def _bootstrap_path():
    """PC 上直接跑本脚本时把仓库根目录加进搜索路径；板上通常不需要。"""
    try:
        import mcpi            # noqa: F401
        return
    except ImportError:
        pass
    try:
        import os
        here = os.path.dirname(os.path.abspath(__file__))
        for p in (os.path.dirname(here), here):
            if p not in sys.path:
                sys.path.insert(0, p)
    except Exception:
        for p in ("..", "."):
            if p not in sys.path:
                sys.path.insert(0, p)


_bootstrap_path()

try:
    IS_MICROPYTHON = sys.implementation.name == "micropython"
except AttributeError:
    IS_MICROPYTHON = "MicroPython" in sys.version

FAILURES = []
CHECKS = [0]


def check(label, got, want):
    CHECKS[0] += 1
    if got != want:
        FAILURES.append("%s: got %r, want %r" % (label, got, want))
        print("  FAIL %s: got %r, want %r" % (label, got, want))
    else:
        print("  ok   %s" % label)


def check_true(label, value):
    check(label, bool(value), True)


# ======================================================================
# A. 环境探针
# ======================================================================

def probe():
    print("== 环境 ==")
    print("  python     : %s" % sys.version.replace("\n", " "))
    print("  platform   : %s" % sys.platform)
    try:
        print("  impl       : %s %s" % (sys.implementation.name, sys.implementation.version))
    except AttributeError:
        pass

    # 剩余堆：MCU 上这个数字决定了要不要砍 entity.py
    try:
        import gc
        gc.collect()
        print("  mem_free   : %d bytes" % gc.mem_free())
    except (ImportError, AttributeError):
        pass

    # 探针 1：编码名大小写。新版 MicroPython 只认 'utf-8'/'utf8'/'ascii'，
    # 见到 'UTF-8' 会抛 LookupError —— 上游 mcpi 正是这么写的。
    try:
        "u".encode("UTF-8")
        upper = "可用"
    except LookupError:
        upper = "不支持（LookupError）→ 上游写法在本环境会崩"
    except UnicodeError:
        upper = "不支持（UnicodeError）"
    print("  encode('UTF-8'): %s" % upper)

    # 探针 2：collections.abc —— 上游 util.py 依赖它，MicroPython 没有
    try:
        import collections.abc  # noqa: F401
        abc = "可用"
    except ImportError:
        abc = "不存在 → 上游 util.py 在本环境会崩"
    print("  collections.abc: %s" % abc)

    # 探针 3/4：socket 与 select 的具体能力（板子上才有意义）
    try:
        import socket
    except ImportError:
        print("  socket     : 不可用（本环境无网络栈，跳过相关探针）")
    else:
        # 注意看的是 socket 对象（类）上的方法，不是模块级函数
        print("  socket     : %s" % ("有 makefile" if hasattr(socket.socket, "makefile")
                                      else "没有 makefile（-> 用自建 readline）"))
        print("  socket.write: %s" % hasattr(socket.socket, "write"))
        try:
            addr = socket.getaddrinfo("127.0.0.1", 4711, socket.AF_INET,
                                      socket.SOCK_STREAM)[0][-1]
            print("  getaddrinfo: 可用 %r" % (addr,))
        except Exception as e:
            print("  getaddrinfo: 不可用 %s" % type(e).__name__)

    try:
        import select
        print("  select     : poll=%s select=%s"
              % (hasattr(select, "poll"), hasattr(select, "select")))
    except ImportError:
        print("  select     : 不可用")

    # 探针 5：浮点精度（ESP32 默认单精度，坐标会有细微差异）
    print("  浮点       : %.10f (repr %s)" % (1.0 / 3.0, repr(1.0 / 3.0)))
    print("")


# ======================================================================
# B. 功能断言
# ======================================================================

def test_flatten():
    print("== flatten（上游在这里依赖 collections.abc，已改写）==")
    from mcpi.util import flatten, flatten_parameters_to_bytestring

    check("嵌套 list/tuple", list(flatten((1, (2, 3), [4]))), [1, 2, 3, 4])
    check("空列表被吃掉", list(flatten(([], 1))), [1])
    check("int", list(flatten((7,))), [7])
    check("None 原样", list(flatten((None,))), [None])
    check("float 原样", list(flatten((1.5,))), [1.5])
    check("str 不被拆开", list(flatten(("ab",))), ["ab"])
    check("bytes 不被拆开", list(flatten((b"ab",))), [b"ab"])
    check("bytearray 不被拆开", list(flatten((bytearray(b"ab"),))), [bytearray(b"ab")])
    check("bool 原样", list(flatten((True,))), [True])
    check("dict 展成键", list(flatten(({"k": 1},))), ["k"])

    from mcpi.vec3 import Vec3

    check("Vec3 展开", list(flatten((Vec3(1, 2, 3),))), [1, 2, 3])
    check("生成器也能展开", list(flatten(((i for i in (5, 6)),))), [5, 6])

    class Cube:
        def __iter__(self):
            return iter((7, 8, 9))

    check("自定义 __iter__ 仍可展开", list(flatten((Cube(),))), [7, 8, 9])

    from mcpi.block import Block, STONE
    from mcpi.entity import PIG

    check("Block 展开", list(flatten((Block(1, 0),))), [1, 0])
    check("Entity 展开", list(flatten((PIG,))), [90])

    print("== 参数序列化（上游写的是 encode('UTF-8')，已改默认 utf-8）==")
    check("int", flatten_parameters_to_bytestring((3,)), b"3")
    check("float", flatten_parameters_to_bytestring((1.5,)), b"1.5")
    check("str", flatten_parameters_to_bytestring(("hi",)), b"hi")
    check("bytes 原样", flatten_parameters_to_bytestring((b"hi",)), b"hi")
    check("bytearray 转 bytes", flatten_parameters_to_bytestring((bytearray(b"hi"),)), b"hi")
    check("None -> b'None'", flatten_parameters_to_bytestring((None,)), b"None")
    check("多个参数用逗号连", flatten_parameters_to_bytestring((1, "a", b"b")), b"1,a,b")
    check("Block", flatten_parameters_to_bytestring((STONE,)), b"1,0")
    check("中文走 UTF-8", flatten_parameters_to_bytestring(("中",)), "中".encode())
    check_true("中文编码不为空", len(flatten_parameters_to_bytestring(("中",))) == 3)


def test_vec3():
    print("== Vec3 ==")
    from mcpi.vec3 import Vec3

    a, b = Vec3(1, 2, 3), Vec3(4, 5, 6)
    check("初始值", (a.x, a.y, a.z), (1, 2, 3))
    check("加法", a + b, Vec3(5, 7, 9))
    check("减法", b - a, Vec3(3, 3, 3))
    check("取负", -a, Vec3(-1, -2, -3))
    check("乘法", a * 2, Vec3(2, 4, 6))
    check("lengthSqr", a.lengthSqr(), 14)
    check("clone 相等", a.clone(), a)
    check("迭代", list(a), [1, 2, 3])
    check("repr", repr(a), "Vec3(1,2,3)")
    c = Vec3(1, 2, 3)
    c.ifloor()
    check("ifloor", (c.x, c.y, c.z), (1, 2, 3))
    c = Vec3(1.7, -1.7, 2.2)
    c.ifloor()
    check("ifloor 负数向零取整", (c.x, c.y, c.z), (1, -1, 2))
    r = Vec3(1, 0, 0)
    r.rotateLeft()
    check("rotateLeft", (r.x, r.z), (0, -1))


def test_constants():
    print("== 方块 / 实体常量表（方案 A 决定保留）==")
    from mcpi import block
    from mcpi import entity

    block_names = [n for n in dir(block) if n.isupper()]
    entity_names = [n for n in dir(entity) if n.isupper()]
    print("  方块常量 %d 个，实体常量 %d 个" % (len(block_names), len(entity_names)))
    check_true("方块常量表完整（>90）", len(block_names) > 90)
    check_true("实体常量表完整（>70）", len(entity_names) > 70)

    check("STONE", block.STONE.id, 1)
    check("GLOWING_OBSIDIAN", block.GLOWING_OBSIDIAN.id, 246)
    check("AIR 的 data", block.AIR.data, 0)
    check("WOOL 可带 data", block.WOOL.withData(14).data, 14)
    check("Block 相等", block.STONE == block.Block(1, 0), True)
    check("Block hash", hash(block.STONE), (1 << 8) + 0)
    check("Entity id", entity.PIG.id, 90)
    check("Entity name", entity.ENDERMAN.name, "ENDERMAN")
    check("Entity repr", repr(entity.PIG), "Entity(90)")


def test_events():
    print("== 事件对象 ==")
    from mcpi.event import BlockEvent, ChatEvent, ProjectileEvent

    hit = BlockEvent.Hit(1, 2, 3, 4, 5)
    check("BlockEvent.type", hit.type, BlockEvent.HIT)
    check("BlockEvent.pos", (hit.pos.x, hit.pos.y, hit.pos.z), (1, 2, 3))
    check("BlockEvent.face", hit.face, 4)
    check("BlockEvent.entityId", hit.entityId, 5)
    check_true("BlockEvent.repr 含 HIT", "HIT" in repr(hit))

    post = ChatEvent.Post(7, "hello")
    check("ChatEvent.message", post.message, "hello")
    check("ChatEvent.entityId", post.entityId, 7)

    proj = ProjectileEvent.Hit(1, 2, 3, 4, "steve", "ZOMBIE")
    check("ProjectileEvent.originName", proj.originName, "steve")
    check("ProjectileEvent.targetName", proj.targetName, "ZOMBIE")


def test_minecraft_module():
    print("== minecraft 模块（需要 socket，板子上才有意义）==")
    try:
        came_from = None
        try:
            from mcpi.minecraft import intFloor, Minecraft, Vec3, Block
        except ImportError:
            # 部分 port 只提供 usocket
            import mcpi.connection as _c
            import mcpi.minecraft as _m
            came_from = _c.__name__
            intFloor, Minecraft, Vec3, Block = (_m.intFloor, _m.Minecraft,
                                                _m.Vec3, _m.Block)
        if came_from:
            print("  （经 %s 导入）" % came_from)
    except ImportError as e:
        print("  SKIP：本环境没有 socket/USocket，无法导入 mcpi.minecraft（%s）" % e)
        return

    check("intFloor 取整", intFloor(1.7, 2.2, -0.4), [1, 2, -1])
    check("intFloor 嵌套", intFloor([1.9, [2.9]]), [1, 2])
    check("intFloor 传 Vec3", intFloor(Vec3(1.5, 2.5, 3.5)), [1, 2, 3])
    check_true("Minecraft.create 存在", callable(Minecraft.create))

    from mcpi import minecraft as m
    for name in ("CmdPlayer", "CmdEntity", "CmdCamera", "CmdEvents",
                 "CmdPositioner", "BlockEvent", "ChatEvent", "ProjectileEvent"):
        check_true("minecraft.%s 存在" % name, hasattr(m, name))


def main():
    print("")
    probe()
    test_flatten()
    test_vec3()
    test_constants()
    test_events()
    test_minecraft_module()

    print("")
    if FAILURES:
        print("MPCOMPAT-FAILED：%d/%d 项失败" % (len(FAILURES), CHECKS[0]))
        for f in FAILURES:
            print("  - %s" % f)
        if not IS_MICROPYTHON:
            raise SystemExit(1)
    else:
        print("MPCOMPAT-OK：%d 项断言全部通过（%s）"
              % (CHECKS[0], "MicroPython" if IS_MICROPYTHON else "CPython"))


main()
