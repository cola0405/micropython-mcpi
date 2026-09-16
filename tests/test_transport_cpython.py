"""
mcpi（MicroPython 兼容版）端到端测试 —— 在 CPython 上跑。

不需要真实的 Minecraft 服务端：tests/mock_server.py 会在本地起一个
实现了 RaspberryJuice 行协议的 mock 服务端，逐个验证：

  * 线上请求的精确字节格式（含 UTF-8 中文编码）
  * 世界读写 / 玩家 / 实体 / 事件四类 API 的完整链路
  * Fail → RequestError 的失败路径，以及 drain() 对脏响应的清理
  * 回归用例：一次响应两行、长响应、非法 UTF-8 降级
  * 静态守卫：改写的文件里不允许再出现 MicroPython 不支持的写法

注意：RaspberryJuice 对 **set 类命令不回任何响应**（只对查询类命令回数据、
出错回 Fail），所以 send() 之后不能去 receive()。

运行：
    python tests/test_transport_cpython.py
    python tests/test_transport_cpython.py multiline    # 只跑名字含 multiline 的用例
"""

import os
import socket
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from mock_server import MockServer                          # noqa: E402
from mcpi import minecraft, block, entity                   # noqa: E402
from mcpi.minecraft import Minecraft, Vec3, Block           # noqa: E402
from mcpi.connection import Connection, RequestError, ResponseTooLarge  # noqa: E402

HOST = "127.0.0.1"

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def connect(srv, **kw):
    """连到 mock 服务端。默认带 5 秒超时，避免出错时挂死。"""
    kw.setdefault("timeout", 5)
    return Connection(HOST, srv.port, **kw)


def mc_of(srv):
    return Minecraft.create(HOST, srv.port)


def assert_sent(srv, expected, timeout=3.0):
    """断言客户端确实发出了这行字节。

    请求是 mock 服务端的处理线程记录的，所以必须等一下，不能立刻读。
    """
    deadline = time.time() + timeout
    seen = None
    while time.time() < deadline:
        with srv._lock:
            raws = [r.raw for r in srv.requests]
        if raws:
            seen = raws[-1]
        if expected in raws:
            return
        time.sleep(0.002)
    raise AssertionError("期望发出 %r，实际收到的是 %r" % (expected, seen))


def closed_port():
    """拿一个确定没人监听的端口"""
    s = socket.socket()
    s.bind((HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ----------------------------------------------------------------------
# 纯逻辑（不需要网络）
# ----------------------------------------------------------------------

@test
def t01_flatten_and_encoding(srv):
    from mcpi.util import flatten, flatten_parameters_to_bytestring

    assert list(flatten((1, (2, 3), [4], Vec3(5, 6, 7)))) == [1, 2, 3, 4, 5, 6, 7]

    # bytes / str 必须原样保留 —— 上游会把 bytes 拆成 97,98,99
    assert list(flatten((b"ab",))) == [b"ab"]
    assert list(flatten(("ab",))) == ["ab"]
    assert list(flatten((None, 1.5))) == [None, 1.5]

    # 标量、Block、Entity 都能被正确序列化
    assert flatten_parameters_to_bytestring((Block(1, 0), Vec3(1, 2, 3))) == b"1,0,1,2,3"
    assert flatten_parameters_to_bytestring((entity.PIG,)) == b"90"

    # 上游文档承诺的能力：自定义类型只要实现 __iter__ 就能被展开
    class Cube:
        def __iter__(self):
            return iter((7, 8, 9))

    assert list(flatten((Cube(),))) == [7, 8, 9]

    # 非 ASCII 走 UTF-8，且编码名没写错（大写会抛 LookupError）
    assert flatten_parameters_to_bytestring(("中文",)) == "中文".encode()


@test
def t02_vec3_and_constants(srv):
    a, b = Vec3(1, 2, 3), Vec3(4, 5, 6)
    assert (a + b) == Vec3(5, 7, 9)
    assert (b - a) == Vec3(3, 3, 3)
    assert (a * 2) == Vec3(2, 4, 6)
    assert -a == Vec3(-1, -2, -3)
    assert a.lengthSqr() == 14
    assert list(a) == [1, 2, 3]
    assert repr(Vec3(1, 2, 3)) == "Vec3(1,2,3)"

    # 方块 / 实体常量表按决策保留（内存换完整性）
    assert block.STONE.id == 1 and block.GLOWING_OBSIDIAN.id == 246
    assert block.AIR == Block(0)
    assert entity.PIG.id == 90 and entity.ENDER_CRYSTAL.id == 200


# ----------------------------------------------------------------------
# 协议链路
# ----------------------------------------------------------------------

@test
def t03_wire_format(srv):
    conn = connect(srv)

    conn.send(b"world.setBlock", 1, 2, 3, block.STONE)
    assert_sent(srv, b"world.setBlock(1,2,3,1,0)\n")     # 命令(逗号分隔参数)\n

    conn.send(b"chat.post", "hello world")
    assert_sent(srv, b"chat.post(hello world)\n")

    # set 类命令静默：不去 receive 就不会误读别人的响应
    assert conn._buf == b"" and conn._readable(50) is False


@test
def t04_minecraft_world_ops(srv):
    mc = mc_of(srv)

    mc.setBlock(0, 0, 0, block.STONE.id, 0)
    assert_sent(srv, b"world.setBlock(0,0,0,1,0)\n")
    assert mc.getBlock(0, 0, 0) == block.STONE.id
    assert mc.getBlockWithData(0, 0, 0) == Block(block.STONE.id, 0)

    mc.setBlocks(0, 0, 0, 2, 2, 2, block.GOLD_BLOCK.id)
    assert_sent(srv, b"world.setBlocks(0,0,0,2,2,2,41)\n")
    ids = list(mc.getBlocks(0, 0, 0, 2, 2, 2))
    assert len(ids) == 27
    assert ids[0] == block.GOLD_BLOCK.id

    mc.setBlock(5, 10, 5, block.DIRT)
    assert mc.getHeight(5, 5) == 10

    assert mc.getPlayerEntityIds() == [1, 2]
    assert mc.getPlayerEntityId("steve") == 1
    assert mc.spawnEntity(0, 0, 0, entity.PIG) == 101
    assert_sent(srv, b"world.spawnEntity(0,0,0,90)\n")

    mc.postToChat("hi")
    assert_sent(srv, b"chat.post(hi)\n")

    mc.setting("world_immutable", True)
    assert_sent(srv, b"world.setting(world_immutable,1)\n")

    # 告示牌文本会把逗号/括号换成协议安全字符
    mc.setSign(1, 2, 3, block.SIGN_STANDING.id, 0, "a,b", "(c)")
    assert_sent(srv, b"world.setSign(1,2,3,63,0,a;b,[c])\n")


@test
def t05_fail_raises(srv):
    conn = connect(srv)

    try:
        conn.sendReceive(b"test.fail")
    except RequestError as e:
        assert "test.fail" in str(e)
    else:
        raise AssertionError("服务器回 Fail 时必须抛 RequestError")

    # 未知命令同样走 Fail
    try:
        conn.sendReceive(b"world.noSuchThing")
    except RequestError:
        pass
    else:
        raise AssertionError("未知命令应该抛 RequestError")

    # 异常之后连接必须还能继续用
    assert conn.sendReceive(b"test.echo", 42) == "42"


@test
def t06_multiline_no_data_loss(srv):
    """回归用例：上游每次 receive 都新建 makefile，第二行会被吞掉。"""
    conn = connect(srv)
    conn.send(b"test.multiline")
    assert conn.receive() == "OK"
    assert conn.receive() == "SECOND"      # 上游在这里会卡死
    assert conn._buf == b""


@test
def t07_drain_clears_stale(srv):
    conn = connect(srv)
    conn.send(b"test.stale")
    assert conn.receive() == "OK"
    time.sleep(0.1)                        # 确保残留那行已经到达
    assert conn.drain() > 0

    conn.send(b"test.echo", 42)
    assert conn.receive() == "42"


@test
def t08_long_response(srv):
    """大范围 getBlocks 返回长行，必须完整收下（缓冲读而不是逐字节卡住）。"""
    mc = mc_of(srv)
    mc.setBlocks(0, 0, 0, 7, 7, 7, block.STONE.id)
    ids = list(mc.getBlocks(0, 0, 0, 7, 7, 7))
    assert len(ids) == 512
    assert set(ids) == {block.STONE.id}

    conn = connect(srv)
    line = conn.sendReceive(b"test.bigline", 2000)
    assert line.count(",") == 1999
    assert len(line) == 3999


@test
def t09_utf8_and_bad_bytes(srv):
    conn = connect(srv)
    msg = "中文测试"

    conn.send(b"chat.post", msg)
    assert_sent(srv, b"chat.post(" + msg.encode("utf-8") + b")\n")

    # 服务端回显非 ASCII，客户端要能解回 str
    assert conn.sendReceive(b"test.echo", msg) == msg

    # 非法 UTF-8 字节不能把客户端搞崩，降级成 ASCII 安全表示
    assert conn.sendReceive(b"test.rawbytes") == "??OK"


@test
def t10_player_position(srv):
    mc = mc_of(srv)

    pos = mc.player.getPos()
    assert isinstance(pos, Vec3)
    assert (pos.x, pos.y, pos.z) == (0.5, 64.0, -1.25)

    tile = mc.player.getTilePos()
    assert (tile.x, tile.y, tile.z) == (0, 64, -2)

    assert mc.player.getRotation() == 90.0
    assert mc.player.getPitch() == 12.5
    d = mc.player.getDirection()
    assert (d.x, d.y, d.z) == (0.0, 0.0, 1.0)

    mc.player.setPos(1, 2, 3)
    assert_sent(srv, b"player.setPos(1,2,3)\n")

    # setTilePos 会向下取整
    mc.player.setTilePos(1.7, 2.2, -0.4)
    assert_sent(srv, b"player.setTile(1,2,-1)\n")

    mc.player.setRotation(45.0)
    assert_sent(srv, b"player.setRotation(45.0)\n")
    mc.player.setPitch(10.0)
    assert_sent(srv, b"player.setPitch(10.0)\n")
    mc.player.setting("autojump", False)
    assert_sent(srv, b"player.setting(autojump,0)\n")


@test
def t11_entities(srv):
    mc = mc_of(srv)

    ents = mc.entity.getEntities(1)
    assert len(ents) == 2
    assert ents[0][0] == 10 and ents[0][1] == 54
    assert ents[0][2] == "ZOMBIE" and ents[0][3] == 1.5
    assert ents[1][2] == "PIG" and ents[1][4] == 64.0

    assert mc.entity.getName(1) == "steve"
    assert mc.entity.removeEntities(1) == 2
    assert mc.removeEntities() == 2
    assert mc.removeEntity(1) == 1

    types = mc.getEntityTypes()
    assert len(types) == 2
    assert types[0].id == 54 and types[0].name == "ZOMBIE"
    assert types[1] == entity.PIG


@test
def t12_events(srv):
    mc = mc_of(srv)

    srv.block_hits.append((1, 2, 3, 4, 1))
    hits = mc.player.pollBlockHits()
    assert len(hits) == 1
    assert (hits[0].pos.x, hits[0].pos.y, hits[0].pos.z) == (1, 2, 3)
    assert hits[0].face == 4 and hits[0].entityId == 1

    srv.chat_posts.append((1, "hi there"))
    posts = mc.player.pollChatPosts()
    assert len(posts) == 1 and posts[0].message == "hi there"

    srv.projectile_hits.append((1, 2, 3, 4, "steve", "ZOMBIE"))
    ph = mc.player.pollProjectileHits()
    assert len(ph) == 1 and ph[0].originName == "steve" and ph[0].targetName == "ZOMBIE"

    # 实体版事件会带上 entityId 参数
    assert len(mc.entity.pollBlockHits(1)) == 1
    assert_sent(srv, b"entity.events.block.hits(1)\n")

    mc.events.clearAll()
    assert_sent(srv, b"events.clear()\n")
    mc.player.clearEvents()
    assert_sent(srv, b"player.events.clear()\n")


@test
def t13_max_line_guard(srv):
    conn = connect(srv, max_line=64)
    try:
        conn.sendReceive(b"test.bigline", 500)
    except ResponseTooLarge as e:
        assert isinstance(e, RequestError)     # 子类，上游 except 依然有效
    else:
        raise AssertionError("超长响应必须抛 ResponseTooLarge")

    # 默认上限足够跑通同一个请求
    assert connect(srv).sendReceive(b"test.bigline", 500).count(",") == 499


@test
def t14_timeout_and_close(srv):
    conn = connect(srv, timeout=2)
    assert conn.sendReceive(b"test.echo", 7) == "7"

    # 连不上的地址要抛 OSError，而不是安静地假装成功
    try:
        Connection(HOST, closed_port(), timeout=1)
    except OSError:
        pass
    else:
        raise AssertionError("连接失败必须抛 OSError")

    conn.close()
    try:
        conn.sendReceive(b"test.echo", 1)
    except OSError:
        pass
    else:
        raise AssertionError("关闭后的 socket 必须抛 OSError")


@test
def t15_public_api_surface(srv):
    """决策 A 的核心承诺：对外 API 一个字没变。"""
    mc = mc_of(srv)
    for name in ("Minecraft", "CmdPlayer", "CmdEntity", "CmdCamera", "CmdEvents",
                 "CmdPositioner", "Vec3", "Block", "Entity",
                 "BlockEvent", "ChatEvent", "ProjectileEvent", "intFloor"):
        assert hasattr(minecraft, name), "minecraft.%s 丢了" % name

    for attr in ("camera", "entity", "player", "events", "conn"):
        assert hasattr(mc, attr), "Minecraft.%s 丢了" % attr

    for name in ("getBlock", "getBlockWithData", "getBlocks", "setBlock",
                 "setBlocks", "setSign", "spawnEntity", "getHeight",
                 "getPlayerEntityIds", "getPlayerEntityId", "saveCheckpoint",
                 "restoreCheckpoint", "postToChat", "setting",
                 "getEntityTypes", "getEntities", "removeEntity", "removeEntities",
                 "create"):
        assert callable(getattr(mc, name)), "Minecraft.%s 不是可调用的" % name

    # 上游的类属性保留为 str，不破坏读它的代码
    from mcpi.connection import Connection as C
    assert C.RequestFailed == "Fail"
    assert C.read_chunk == 256
    # create 的默认参数也保持上游值
    assert Minecraft.create.__defaults__ == ("localhost", 4711)


@test
def t16_static_guard(srv):
    """静态守卫：用 AST 检查（自动忽略注释与文档字符串）。

    * 改写的两个文件里不允许再出现 MicroPython 不支持的 import / 属性；
    * 全包不允许出现大写或非 utf-8 的编码名（新版 MicroPython 会抛
      LookupError，例如 encode("UTF-8")）。
    """
    import ast

    def parse(rel):
        with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as fh:
            return ast.parse(fh.read(), rel)

    banned_imports = ("sys", "collections", "collections.abc")
    banned_attrs = ("makefile", "stderr", "Iterable", "abc")

    for rel in ("mcpi/connection.py", "mcpi/util.py"):
        for node in ast.walk(parse(rel)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in banned_imports, \
                        "%s 仍在 import %s" % (rel, alias.name)
            elif isinstance(node, ast.ImportFrom):
                assert node.module not in banned_imports, \
                    "%s 仍 from %s import" % (rel, node.module)
            elif isinstance(node, ast.Attribute):
                assert node.attr not in banned_attrs, \
                    "%s 仍在使用 .%s" % (rel, node.attr)

    package_files = ["mcpi/" + name for name in
                     ("connection.py", "util.py", "minecraft.py",
                      "block.py", "entity.py", "vec3.py", "event.py")]
    for rel in package_files:
        for node in ast.walk(parse(rel)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                low = node.value.lower()
                if low in ("utf-8", "utf8"):
                    assert node.value in ("utf-8", "utf8"), \
                        "%s 里有大小写错误的编码名 %r" % (rel, node.value)


@test
def t17_drain_protects_next_read(srv):
    """为什么要保守地保留 drain()：fire-and-forget 命令也可能留下 Fail。

    camera.* 是 Pi Edition 专有，RaspberryJuice 不支持 → 回 "Fail"。
    调用方不会去读它，于是它留在缓冲区里；drain() 会在下一次发送前把它清掉。
    这里同时验证"清掉"和"不清就会出事"两半。
    """
    mc = mc_of(srv)

    mc.camera.setNormal(1)
    assert_sent(srv, b"camera.mode.setNormal(1)\n")
    time.sleep(0.1)                          # 让 Fail 到达
    assert mc.player.getTilePos().y == 64    # 没被残留的 Fail 污染

    # 关掉 drain 复现污染：残留的 Fail 会被下一条读命令吃掉
    mc.conn.drain = lambda: 0
    mc.camera.setFixed()
    assert_sent(srv, b"camera.mode.setFixed()\n")
    time.sleep(0.1)
    try:
        mc.player.getTilePos()
    except RequestError:
        pass
    else:
        raise AssertionError("关掉 drain 后应被残留的 Fail 污染")


# ----------------------------------------------------------------------

def main():
    filters = sys.argv[1:]
    passed = failed = 0
    failures = []
    for fn in TESTS:
        if filters and not any(f in fn.__name__ for f in filters):
            continue
        srv = MockServer()
        try:
            fn(srv)
        except Exception:
            failed += 1
            failures.append(fn.__name__)
            print("FAIL  %s" % fn.__name__)
            traceback.print_exc()
        else:
            passed += 1
            print("ok    %s" % fn.__name__)
        finally:
            srv.stop()

    print("\n%d passed, %d failed" % (passed, failed))
    if failures:
        print("failed: %s" % ", ".join(failures))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
