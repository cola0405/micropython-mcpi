# Change Log

## 2026-09-16 MicroPython 移植版（本次修改）

以 mcpi 1.2.1 为基线，只改 `mcpi/connection.py` 与 `mcpi/util.py`，对外 API 完全不变。

+ `util.py`：去掉 `collections.abc` 依赖（MicroPython 的 collections 没有 abc 子模块），
  改为标量白名单 + `iter()` 探测；`encode("UTF-8")` 改为默认 utf-8（新版 MicroPython
  会校验编码名并拒绝大写变体）
+ `connection.py`：文本模式 `makefile("r")` 改为自建带缓冲的 readline（MicroPython 的
  makefile 只支持二进制模式）；`select.select()` 改为优先 `select.poll()`；
  `sys.stderr.write()` 改为 `Connection.debug` + `print()`
+ 修复上游缺陷：每次 `receive()` 新建 file 对象导致缓冲预读的响应被丢弃（多行响应会丢数据）
+ 修复上游缺陷：`flatten()` 会把 `bytes` 拆成一个个 int 发出去
+ 新增可选能力：`Connection(timeout=)`、`Connection(max_line=)` + `ResponseTooLarge`、`close()`
+ 新增测试与工具：17 个端到端用例（自带 mock 服务端）、69 项兼容自检（CPython/板上双跑）、
  wasm 版 MicroPython 冒烟验证脚本

## 2021-10-31 v1.2.1

+ Python 3.10 compatibility fix

## 2020-04-19 v1.2.0

+ Brought up to date with latest version of RaspberryJuice 1.12.1

## 2018-05-01 v1.1.0 

+ packaged and released onto [PyPI](https://pypi.org)
+ it seemed ridiculous calling this v1.0.0 given the maturity of this library, so it has become v1.1.0

## in the past v1.0.0

+ the library was created it was used but never packaged.