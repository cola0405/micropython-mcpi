"""
示例脚本的"干跑"测试：把 examples/ 里的脚本放到假板子环境里真跑一遍。

做到这一点只需要两件事：
  1. 往 sys.modules 塞一个假的 `network` 模块（WLAN 永远连得上）；
  2. 把 socket.socket.connect 重定向到本地 mock 服务端 —— 这样示例里写的
     真实 IP/端口不用改，连的还是本地 mock。

目的：示例不会"写完就腐烂"。它至少保证了示例里用到的 mcpi API 是真实可用的，
以及放下去的方块确实出现在世界状态里。

注意：esp32_hello.py 里有个 60 秒的事件轮询循环，不适合自动跑，所以不在此列。

运行：python tests/test_examples.py
"""

import os
import socket
import sys
import traceback
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from mock_server import MockServer                      # noqa: E402


def fake_network():
    """一个永远连上的 network 模块"""
    mod = types.ModuleType("network")

    class WLAN:
        STA_IF = 0
        AP_IF = 1

        def __init__(self, interface=None):
            self.interface = interface

        def active(self, *args):
            return True

        def isconnected(self):
            return True

        def connect(self, *args):
            pass

        def ifconfig(self):
            return ("192.168.1.50", "255.255.255.0", "192.168.1.1", "8.8.8.8")

        def status(self):
            return 5

    mod.WLAN = WLAN
    # 真实固件里 STA_IF/AP_IF 在模块级（新固件在 WLAN 上也有一份），两边都给上
    mod.STA_IF = 0
    mod.AP_IF = 1
    return mod


def redirect_socket(port):
    """把所有 connect 重定向到本地 mock 服务端"""
    real_connect = socket.socket.connect

    def patched(self, address):
        if isinstance(address, tuple):
            address = ("127.0.0.1", port)
        return real_connect(self, address)

    socket.socket.connect = patched
    return real_connect


def dry_run(path, srv):
    """在假板子环境里执行一个示例脚本"""
    old_network = sys.modules.get("network")
    old_connect = socket.socket.connect
    sys.modules["network"] = fake_network()
    redirect_socket(srv.port)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        code = compile(source, path, "exec")
        exec(code, {"__name__": "__main__", "__file__": path})
    finally:
        socket.socket.connect = old_connect
        if old_network is None:
            sys.modules.pop("network", None)
        else:
            sys.modules["network"] = old_network


TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def t01_setblock_quickstart(srv):
    path = os.path.join(ROOT, "examples", "setblock_quickstart.py")
    dry_run(path, srv)

    # mock 的 player.getTile 返回 0,64,-2，示例以它为基准
    px, py, pz = 0, 64, -2

    # 4.1 只传 Block 对象 -> id 41(data 0)
    assert srv.world.get((px + 1, py, pz)) == (41, 0), \
        "金块没放对: %r" % (srv.world.get((px + 1, py, pz)),)

    # 4.2 逐格放羊毛 -> data 记录颜色
    for i in range(5):
        got = srv.world.get((px + 2, py + i, pz))
        assert got == (35, i), "羊毛第 %d 格不对: %r" % (i, got)

    # 4.3 setBlocks 填长方体 -> 3x3x3 = 27 格石头
    filled = [(x, y, z)
              for x in range(px + 3, px + 6)
              for y in range(py, py + 3)
              for z in range(pz, pz + 3)
              if srv.world.get((x, y, z)) == (1, 0)]
    assert len(filled) == 27, "石头长方体只填了 %d 格" % len(filled)

    # 消息也发出去过
    sent = [r.raw for r in srv.requests]
    assert b"chat.post([ESP32] \xe6\x88\x91\xe6\x9d\xa5\xe4\xba\x86)\n" in sent


@test
def t02_examples_compile(srv):
    """所有示例至少语法/API 引用是合法的（不能 exec 的只做编译检查）"""
    names = [n for n in os.listdir(os.path.join(ROOT, "examples"))
             if n.endswith(".py")]
    assert names, "examples/ 下没有示例"
    for name in names:
        path = os.path.join(ROOT, "examples", name)
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        compile(src, path, "exec")          # 语法错会在这里抛
        assert "import network" in src, "%s 没有连 WiFi 的代码?" % name
        print("  ok   %s 可编译、含 WiFi 连接" % name)


def main():
    filters = sys.argv[1:]
    passed = failed = 0
    for fn in TESTS:
        if filters and not any(f in fn.__name__ for f in filters):
            continue
        srv = MockServer()
        try:
            fn(srv)
        except Exception:
            failed += 1
            print("FAIL  %s" % fn.__name__)
            traceback.print_exc()
        else:
            passed += 1
            print("ok    %s" % fn.__name__)
        finally:
            srv.stop()
    print("\n%d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
