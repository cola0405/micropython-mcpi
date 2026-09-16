"""
参数展开与序列化工具（CPython / MicroPython 双兼容）。

本文件源自上游 mcpi 1.2.1 的 `mcpi/util.py`，函数名与语义保持不变。
改动点（每处都标了 [MP] 注释）：

1. [MP] 去掉 `collections.abc` 依赖。MicroPython 的 collections 模块只有
   deque / namedtuple / OrderedDict，既没有 abc 子模块，也没有 Iterable，
   上游 `isinstance(e, collections.Iterable)` 在板子上会直接
   AttributeError —— 而 flatten() 是所有 send() 的必经路径，等于全库瘫痪。
   这里改成"标量白名单 + iter() 探测"，用 try/except TypeError 判断是否
   可展开，既保留上游"自定义类型实现 __iter__ 即可被展开"的能力，又完全
   不依赖 abc。

2. [MP] `str(m).encode("UTF-8")` 的大写编码名不可用。MicroPython 只支持
   utf-8 / utf8 / ascii 三种写法，且新版固件会校验编码名，遇到 'UTF-8'
   这种大小写变体会直接抛 LookupError（旧固件曾静默按 utf8 处理，所以这是
   个"换块新板子就崩"的隐性炸弹）。改为不传参数（默认 utf-8）。

3. 顺带修掉上游 flatten 会把 bytes 拆散的问题：上游判据是
   `isinstance(e, Iterable) and not isinstance(e, str)`，而 bytes 是
   Iterable 又不是 str，于是 b"abc" 会被拆成 97、98、99 三个 int 发出去。
"""

# 这些类型直接当标量发出去，不做展开。
_SCALARS = (int, str, bytes, bytearray)


def flatten(l):
    """把嵌套的 iterable 参数展开成一维。

    标量（int / str / bytes / bytearray / None / float 等）原样产出；
    list、tuple 以及实现了 __iter__ 的对象（Vec3 / Block / Entity /
    自定义类型）递归展开。

    例：flatten(((1, 2), [3, 4], Vec3(5, 6, 7))) 依次产出 1..7
    """
    for e in l:
        if e is None or isinstance(e, _SCALARS):
            yield e
            continue
        try:
            it = iter(e)
        except TypeError:
            yield e            # 不可迭代（float、bool 之外的普通对象等）
        else:
            for ee in flatten(it):
                yield ee


def flatten_parameters_to_bytestring(l):
    return b",".join(map(_misc_to_bytes, flatten(l)))


def _misc_to_bytes(m):
    """
    Convert an arbitrary object into a string encoded as a UTF-8 series of bytes.

    See `Connection.send` for more details.

    已经是 bytes 的原样返回；str 按 UTF-8 编码；其余（int / float /
    Vec3 / Block / Entity 等）走 str() 再编码。

    [MP] 注意不要写成 encode("UTF-8")：MicroPython 只认 'utf-8' / 'utf8' /
    'ascii'，大写变体会抛 LookupError。
    """
    if isinstance(m, bytes):
        return m
    if isinstance(m, bytearray):
        return bytes(m)
    if isinstance(m, str):
        return m.encode()
    return str(m).encode()
