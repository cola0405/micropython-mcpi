"""
Minecraft: Pi edition / RaspberryJuice 连接层（MicroPython 兼容版）。

本文件源自上游 mcpi 1.2.1（Martin O'Hanlon / Aron Nieminen, Mojang AB）的
`mcpi/connection.py`，公开 API 完全保持不变：

    Connection(address, port) / send() / receive() / sendReceive()
    drain() / _send() / RequestError / RequestFailed

为了让同一份代码在 CPython 和 MicroPython 上都能跑，替换了 4 处
MicroPython 不支持的写法（每处都标了 [MP] 注释）：

1. [MP] `socket.makefile("r")` —— MicroPython 的 makefile 只支持二进制模式
   ('rb'/'wb'/'rwb')，文本模式不在支持范围。改为自带缓冲的 readline。
   顺带修掉上游一个真实缺陷：每次 receive 都新建一个 file 对象，其缓冲
   预读进来的后续字节会随对象一起被丢弃，多行/连续响应会丢数据。
2. [MP] `select.select()` —— 官方文档写明"仅部分 port 提供"，推荐 poll()。
   这里优先用 select.poll()，都不可用时保守降级。
3. [MP] `sys.stderr.write()` —— 多数裸机 port 没有 sys.stderr。改为
   Connection.debug 开关 + print()。
4. [MP] 大写编码名 `encode("UTF-8")` 在 util.py 中修掉（见 util.py 说明）。

另外新增（均为向后兼容的增量，不改变默认行为）：
- `timeout=` 构造函数参数：板子上的 WiFi 会抖，可选的读超时；
- `max_line=` / `read_chunk=`：限制单行响应大小，避免大范围 getBlocks
  直接把 MCU 堆内存打爆（超限抛 ResponseTooLarge，它是 RequestError 子类，
  上游 `except RequestError` 的代码无需改动）；
- `close()`：显式释放 socket。

@author: Aron Nieminen, Mojang AB
"""

try:
    import socket
except ImportError:            # 很老的 MicroPython 固件用 usocket
    try:
        import usocket as socket
    except ImportError:
        socket = None          # 没有网络栈的解释器（wasm / 极简构建）也能 import 本包

try:
    import select
except ImportError:
    try:
        import uselect as select
    except ImportError:
        select = None          # 极简固件可能没编译 select

from .util import flatten_parameters_to_bytestring

__all__ = ["Connection", "RequestError", "ResponseTooLarge"]


class RequestError(Exception):
    """服务器回复 Fail（即命令执行失败）"""


class ResponseTooLarge(RequestError):
    """响应行超过 max_line 限制。

    作为 RequestError 的子类，所以既有的 `except RequestError` 依然生效。
    MCU 上堆很小，大范围 getBlocks 会把内存打爆，这里提前给出明确错误。
    """


def _resolve(address, port):
    """把 (地址, 端口) 解析成 connect() 能直接用的 sockaddr。

    MicroPython 官方建议用 getaddrinfo() 而不是直接传元组：部分 port 只
    接受数字 IP。解析不可用或失败时退回原始元组（数字 IP 场景照常工作）。
    """
    try:
        info = socket.getaddrinfo(address, port, socket.AF_INET, socket.SOCK_STREAM)
        return info[0][-1]
    except (OSError, AttributeError, IndexError):
        return (address, port)


def _decode(data):
    """把响应字节解码成 str。

    MicroPython 只内置 utf-8 / ascii 两种编解码，且新版固件会校验编码名，
    所以这里不传编码参数（默认即 utf-8）。遇到非法 UTF-8 字节时降级为
    ASCII 安全表示，避免板子因为一行脏数据直接抛异常。
    """
    try:
        return data.decode()
    except UnicodeError:
        return "".join([chr(c) if c < 128 else "?" for c in data])


class Connection:
    """Connection to a Minecraft Pi game"""

    RequestFailed = "Fail"        # 保留上游的公开属性（str）
    _REQUEST_FAILED = b"Fail"     # 线上实际比较用的是字节
    debug = False                 # 置 True 时打印 drain 丢弃的数据
    read_chunk = 256              # 每次 recv 的字节数（MCU 上别开太大）
    max_line = 16384              # 单行响应上限，超限抛 ResponseTooLarge

    def __init__(self, address, port, timeout=None, max_line=None):
        if socket is None:
            raise OSError("mcpi: this interpreter has no socket support")
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.connect(_resolve(address, port))
        self.lastSent = b""
        self._buf = b""           # 自建读缓冲，替代 makefile 的缓冲
        self._poller = None
        if max_line is not None:
            self.max_line = max_line

        # [MP] MicroPython 的 socket 直接实现了 stream 接口，write() 有
        # "不短写"语义，官方推荐优先用它；CPython 的 socket 没有 write，
        # 退回 sendall()。两者对阻塞 socket 都是"发完为止"。
        self._write = getattr(self.socket, "write", None) or self.socket.sendall

        if timeout is not None:
            try:
                self.socket.settimeout(timeout)
            except (OSError, AttributeError, NotImplementedError):
                pass              # 不是所有 port 都支持 settimeout

    def close(self):
        """关闭连接"""
        try:
            self.socket.close()
        except OSError:
            pass
        self._buf = b""

    # ------------------------------------------------------------------
    # 发送
    # ------------------------------------------------------------------

    def send(self, f, *data):
        """
        Sends data. Note that a trailing newline '\\n' is added here

        协议行的文本按 UTF-8 编码（上游实现即如此，虽然文档字符串里提到过
        CP437；RaspberryJuice 是 Java 端，按 UTF-8 读取请求行）。
        """
        s = b"".join([f, b"(", flatten_parameters_to_bytestring(data), b")", b"\n"])
        self._send(s)

    def _send(self, s):
        """
        The actual socket interaction from self.send, extracted for easier mocking
        and testing
        """
        self.drain()
        self.lastSent = s
        self._write(s)

    # ------------------------------------------------------------------
    # 接收
    # ------------------------------------------------------------------

    def receive(self):
        """Receives data. Note that the trailing newline '\\n' is trimmed"""
        s = self._readline()
        if s == Connection._REQUEST_FAILED:
            raise RequestError("%s failed" % _decode(self.lastSent).strip())
        return _decode(s)

    def _readline(self):
        """从 socket 读一整行（不含换行符）。

        [MP] 替代 makefile("r").readline()：MicroPython 的 makefile 只支持
        二进制模式。自建缓冲的好处是跨调用保留多余的字节 —— 一次 recv 拿到
        两行时，第二行会留在 self._buf 里等下一次 receive() 取，不会像上游
        那样随临时 file 对象一起丢失。
        """
        buf = self._buf
        while True:
            i = buf.find(b"\n")
            if i >= 0:
                line = buf[:i]
                self._buf = buf[i + 1:]
                if line.endswith(b"\r"):
                    return line[:-1]
                return line
            if len(buf) > self.max_line:
                self._buf = b""
                raise ResponseTooLarge(
                    "response line exceeds %d bytes" % self.max_line)
            chunk = self.socket.recv(self.read_chunk)
            if not chunk:
                self._buf = b""
                if buf:
                    return buf       # 对端关闭但还有半行，交出去
                raise OSError("mcpi: connection closed by peer")
            buf += chunk

    def drain(self):
        """丢弃缓冲区与 socket 里残留的响应数据，返回丢弃的字节数。

        上游会把丢弃内容写到 sys.stderr；多数裸机 port 没有 sys.stderr，
        [MP] 这里改成 Connection.debug 开关 + print()。
        """
        dropped = 0
        if self._buf:
            dropped += len(self._buf)
            self._buf = b""
        while self._readable(0):
            try:
                data = self.socket.recv(1500)
            except OSError:
                break
            if not data:
                break
            dropped += len(data)
        if self.debug and dropped:
            print("mcpi: drained %d byte(s) before sending" % dropped)
        return dropped

    def _readable(self, timeout_ms=0):
        """socket 当前是否可读（timeout_ms=0 表示不等待）。

        [MP] select.select() 只在部分 MicroPython port 提供，官方推荐
        poll()。优先 poll，其次 select，两者都没有时保守返回 False，
        此时 drain() 退化为"只清空本地缓冲"，功能仍然正确。
        """
        if select is None:
            return False
        if hasattr(select, "poll"):
            try:
                if self._poller is None:
                    self._poller = select.poll()
                    self._poller.register(self.socket, select.POLLIN)
                return bool(self._poller.poll(timeout_ms))
            except (OSError, ValueError):
                return False
        try:
            return bool(select.select([self.socket], [], [], timeout_ms / 1000.0)[0])
        except (OSError, ValueError):
            return False

    # ------------------------------------------------------------------

    def sendReceive(self, *data):
        """Sends and receive data"""
        self.send(*data)
        return self.receive()
