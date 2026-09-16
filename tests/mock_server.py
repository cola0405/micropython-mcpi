"""
RaspberryJuice 协议的最小 mock 服务端（仅用于 CPython 上跑测试）。

目的：让 mcpi 客户端可以在**没有 Minecraft 服务端**的情况下做端到端验证。

响应规则严格对齐 RaspberryJuice 的真实行为（src/.../RemoteSession.java）：
  * **只有查询类命令才有响应**：get* / poll* / remove* / spawnEntity 等把数据
    发回来；
  * **set 类命令完全静默**：world.setBlock / setBlocks / setSign / chat.post /
    player.set* / events.clear 等执行完什么都不回（源码里根本没有 send("OK")，
    也没有连接欢迎语）；
  * **出错或不支持的命令回 "Fail"**（Pi Edition 专有的 camera.* /
    world.checkpoint.* 在 RaspberryJuice 上就属于"不支持"）。

这一点很关键：因为 set 命令静默，客户端 send() 之后**不能**去 receive()，
否则会读走下一条命令的响应；反过来，如果某条命令回了 Fail 而调用方没读，
这个 Fail 会留在缓冲区里污染下一条读命令 —— 这就是 connection.drain() 存在
的意义。

测试专用命令：
    test.echo(...)       原样回显参数字符串（断言线上编码是否精确）
    test.multiline       一次回两行（回归：上游 makefile 会丢第二行）
    test.stale           回 "OK" + 一行垃圾（验证 drain() 清脏数据）
    test.bigline(n)      回一行 n 个数字（验证 max_line 限制与长行读取）
    test.rawbytes        回非法 UTF-8 字节（验证客户端降级不崩）
    test.fail            回 "Fail"（验证 RequestError）
"""

import socket
import threading


class Raw:
    """让 handler 直接决定回什么字节（不走"字符串 + 换行"的默认路径）"""

    def __init__(self, data):
        self.data = data


class Request:
    """一条收到的请求"""

    def __init__(self, raw, name, params):
        self.raw = raw          # 原始字节（含结尾换行）
        self.name = name        # 如 "world.setBlock"
        self.params = params    # 字符串列表


class MockServer:
    def __init__(self, host="127.0.0.1", port=0):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(4)
        self.host, self.port = self._sock.getsockname()[:2]

        self.requests = []      # 收到的全部请求（按顺序）
        self.world = {}         # (x, y, z) -> (id, data)
        self.block_hits = []    # pollBlockHits 的待返回事件
        self.chat_posts = []    # pollChatPosts 的待返回事件
        self.projectile_hits = []
        self.entities = [
            (10, 54, "ZOMBIE", 1.5, 2.0, 3.5),
            (11, 90, "PIG", -4.25, 64.0, 0.5),
        ]
        self.spawned = 0
        self._lock = threading.Lock()
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------

    def stop(self):
        self._running = False
        try:
            self._sock.close()
        except OSError:
            pass

    def last_request(self):
        return self.requests[-1] if self.requests else None

    def clear_requests(self):
        with self._lock:
            self.requests = []

    # ------------------------------------------------------------------

    def _serve(self):
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _session(self, conn):
        buf = b""
        try:
            while self._running:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    reply = self._reply(line)
                    if reply is False:
                        continue            # 静默：不回任何东西
                    if isinstance(reply, Raw):
                        conn.sendall(reply.data)
                    else:
                        conn.sendall(reply.encode("utf-8") + b"\n")
        except OSError:
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _reply(self, line):
        """返回 False 表示静默不回；否则返回要发送的内容"""
        text = line.decode("utf-8", "replace").rstrip("\r")
        if "(" not in text or not text.endswith(")"):
            return "Fail"
        name, _, rest = text.partition("(")
        rest = rest[:-1]
        params = rest.split(",") if rest else []
        with self._lock:
            self.requests.append(Request(line + b"\n", name, params))
        handler = _HANDLERS.get(name)
        if handler is None:
            return "Fail"                        # 不支持的命令
        try:
            return handler(self, params)
        except Exception:                        # 参数错误等 → Fail（同真实服务端）
            return "Fail"


# ----------------------------------------------------------------------
# 查询类命令：有响应
# ----------------------------------------------------------------------

def _int(p, i, default=0):
    try:
        return int(p[i])
    except (IndexError, ValueError):
        return default


def _h_world_getBlock(s, p):
    return str(s.world.get((_int(p, 0), _int(p, 1), _int(p, 2)), (0, 0))[0])


def _h_world_getBlockWithData(s, p):
    blk = s.world.get((_int(p, 0), _int(p, 1), _int(p, 2)), (0, 0))
    return "%d,%d" % (blk[0], blk[1])


def _h_world_getBlocks(s, p):
    x0, y0, z0, x1, y1, z1 = [_int(p, i) for i in range(6)]
    out = []
    for x in range(min(x0, x1), max(x0, x1) + 1):
        for y in range(min(y0, y1), max(y0, y1) + 1):
            for z in range(min(z0, z1), max(z0, z1) + 1):
                out.append(s.world.get((x, y, z), (0, 0))[0])
    return ",".join([str(v) for v in out])


def _h_world_getHeight(s, p):
    x, z = _int(p, 0), _int(p, 1)
    ys = [k[1] for k in s.world if k[0] == x and k[2] == z]
    return str(max(ys) if ys else 0)


def _h_world_getPlayerIds(s, p):
    return "1|2"


def _h_world_getPlayerId(s, p):
    return "1" if p and p[0] == "steve" else "Fail"


def _h_world_spawnEntity(s, p):
    s.spawned += 1
    return str(100 + s.spawned)


def _format_entities(s):
    return "|".join(["%d,%d,%s,%s,%s,%s" % e for e in s.entities]) + "|"


def _h_world_getEntities(s, p):
    return _format_entities(s)


def _h_world_getEntityTypes(s, p):
    return "54,ZOMBIE|90,PIG|"


def _h_world_removeEntity(s, p):
    return "1"


def _h_world_removeEntities(s, p):
    return str(len(s.entities))


def _h_entity_getName(s, p):
    return "steve"


def _h_player_getPos(s, p):
    return "0.5,64.0,-1.25"


def _h_player_getTile(s, p):
    return "0,64,-2"


def _h_player_getRotation(s, p):
    return "90.0"


def _h_player_getPitch(s, p):
    return "12.5"


def _h_player_getDirection(s, p):
    return "0.0,0.0,1.0"


def _h_block_hits(s, p):
    return "|".join([",".join([str(v) for v in e]) for e in s.block_hits]) + \
        ("|" if s.block_hits else "")


def _h_chat_posts(s, p):
    return "|".join(["%d,%s" % e for e in s.chat_posts]) + \
        ("|" if s.chat_posts else "")


def _h_projectile_hits(s, p):
    return "|".join([",".join([str(v) for v in e]) for e in s.projectile_hits]) + \
        ("|" if s.projectile_hits else "")


# ----------------------------------------------------------------------
# set 类命令：静默（真实服务端不回任何东西）
# ----------------------------------------------------------------------

def _h_world_setBlock(s, p):
    s.world[(_int(p, 0), _int(p, 1), _int(p, 2))] = (_int(p, 3), _int(p, 4, 0))
    return False


def _h_world_setBlocks(s, p):
    x0, y0, z0, x1, y1, z1 = [_int(p, i) for i in range(6)]
    bid, data = _int(p, 6), _int(p, 7, 0)
    for x in range(min(x0, x1), max(x0, x1) + 1):
        for y in range(min(y0, y1), max(y0, y1) + 1):
            for z in range(min(z0, z1), max(z0, z1) + 1):
                s.world[(x, y, z)] = (bid, data)
    return False


def _silent(s, p):
    return False


def _unsupported(s, p):
    """Pi Edition 专有、RaspberryJuice 不支持 → Fail"""
    return "Fail"


_HANDLERS = {
    # ---- 查询类：有响应 ----
    "world.getBlock": _h_world_getBlock,
    "world.getBlockWithData": _h_world_getBlockWithData,
    "world.getBlocks": _h_world_getBlocks,
    "world.getHeight": _h_world_getHeight,
    "world.getPlayerIds": _h_world_getPlayerIds,
    "world.getPlayerId": _h_world_getPlayerId,
    "world.spawnEntity": _h_world_spawnEntity,
    "world.getEntities": _h_world_getEntities,
    "world.getEntityTypes": _h_world_getEntityTypes,
    "world.removeEntity": _h_world_removeEntity,
    "world.removeEntities": _h_world_removeEntities,
    "entity.getName": _h_entity_getName,
    "entity.getPos": _h_player_getPos,
    "entity.getTile": _h_player_getTile,
    "entity.getEntities": _h_world_getEntities,
    "entity.removeEntities": _h_world_removeEntities,
    "entity.events.block.hits": _h_block_hits,
    "entity.events.chat.posts": _h_chat_posts,
    "entity.events.projectile.hits": _h_projectile_hits,
    "player.getPos": _h_player_getPos,
    "player.getTile": _h_player_getTile,
    "player.getDirection": _h_player_getDirection,
    "player.getRotation": _h_player_getRotation,
    "player.getPitch": _h_player_getPitch,
    "player.getEntities": _h_world_getEntities,
    "player.removeEntities": _h_world_removeEntities,
    "player.events.block.hits": _h_block_hits,
    "player.events.chat.posts": _h_chat_posts,
    "player.events.projectile.hits": _h_projectile_hits,
    "events.block.hits": _h_block_hits,
    "events.chat.posts": _h_chat_posts,
    "events.projectile.hits": _h_projectile_hits,

    # ---- set 类：静默 ----
    "world.setBlock": _h_world_setBlock,
    "world.setBlocks": _h_world_setBlocks,
    "world.setSign": _silent,
    "world.setting": _silent,
    "chat.post": _silent,
    "entity.setPos": _silent,
    "entity.setTile": _silent,
    "entity.setDirection": _silent,
    "entity.setRotation": _silent,
    "entity.setPitch": _silent,
    "entity.setting": _silent,
    "entity.events.clear": _silent,
    "player.setPos": _silent,
    "player.setTile": _silent,
    "player.setDirection": _silent,
    "player.setRotation": _silent,
    "player.setPitch": _silent,
    "player.setting": _silent,
    "player.events.clear": _silent,
    "events.clear": _silent,

    # ---- Pi Edition 专有，RaspberryJuice 不支持 → Fail ----
    "camera.mode.setNormal": _unsupported,
    "camera.mode.setFixed": _unsupported,
    "camera.mode.setFollow": _unsupported,
    "camera.setPos": _unsupported,
    "world.checkpoint.save": _unsupported,
    "world.checkpoint.restore": _unsupported,
}


# ----------------------------------------------------------------------
# 测试专用命令
# ----------------------------------------------------------------------

def _t_echo(s, p):
    return ",".join(p)


def _t_multiline(s, p):
    return Raw(b"OK\nSECOND\r\n")


def _t_stale(s, p):
    return Raw(b"OK\nSTALE-DATA\n")


def _t_bigline(s, p):
    n = int(p[0]) if p else 100
    return ",".join([str(i % 10) for i in range(n)])


def _t_rawbytes(s, p):
    return Raw(b"\xff\xfeOK\n")


for _name, _fn in (
    ("test.echo", _t_echo),
    ("test.multiline", _t_multiline),
    ("test.stale", _t_stale),
    ("test.bigline", _t_bigline),
    ("test.rawbytes", _t_rawbytes),
    ("test.fail", lambda s, p: "Fail"),
):
    _HANDLERS[_name] = _fn
