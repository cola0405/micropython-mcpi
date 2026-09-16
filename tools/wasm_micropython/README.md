# tools/wasm_micropython —— 没有硬件时，用真 MicroPython 做冒烟验证

MicroPython 官方**不再提供** Windows / unix 端口的预编译二进制（下载页只有 MCU 固件），
但社区有一个把 MicroPython 编译成 WebAssembly 的包，可以在 Node 里跑真解释器：

> `@yeliulee/micropython-wasm` v1.0.0（MicroPython 1.17，2022 年发布，MIT）
> 官方仓库：https://github.com/yeliulee/micropython-wasm

它的价值在于：**不用插板子就能验证语法/语义兼容性**，例如确认
`collections.abc` 不存在、`select.poll` 存在、`str.encode` 的行为差异。

## 用法

```bash
cd tools/wasm_micropython
curl -sLO https://registry.npmjs.org/@yeliulee/micropython-wasm/-/micropython-wasm-1.0.0.tgz
tar xzf micropython-wasm-1.0.0.tgz && mv package ./package
node run_tests.js ../../          # 参数是仓库根目录
```

输出示例（节选）：

```
[helpers] ret=0
[load mcpi.util] ret=0
...
[load mcpi.minecraft] ret=0
== 环境 ==
  impl       : micropython (1, 17, 0)
  mem_free   : 75776 bytes
  encode('UTF-8'): 可用
  collections.abc: 不存在 → 上游 util.py 在本环境会崩
  select     : poll=True select=True
[test_flatten] ret=0
```

## 这个 harness 踩过的坑（改脚本前先看这里）

1. **Emscripten 在 Node 下取不到 .wasm**：它用 `fetch()` 拿相对路径，报
   `unknown scheme`。脚本里覆盖了 `globalThis.fetch` 直接读本地文件。
2. **必须先 `mp_js_init(stack_size)`**，否则所有 `mp_js_do_str` 都返回 1 并且只吐一个 `c: `。
3. **`mp_js_do_str` 是按单条语句编译的**：`try:` / `class` 这类复合语句会 SyntaxError。
   要用 `exec()` 包一层才能跑完整程序 —— 但 JSON 转义后的嵌套引号又会踩到 cwrap 的字符串编组，
   所以最终方案是"分块喂 + 直接执行"。
4. **解释器状态跨 `mp_js_do_str` 调用是保持的**，所以可以一个模块一次调用地注入。
5. **`exec(src, obj.__dict__)` 会 TypeError**：MicroPython 上要给 exec 传普通 dict，
   再用 `setattr` 把 key 组装成模块对象。
6. **这个 build 没有文件系统**（`FORCE_FILESYSTEM=0`），不能往板上拷文件，
   所以走 `sys.modules` 注入来验证相对 import。
7. **JS 侧栈很小**：一次性跑完整个测试脚本会 `RangeError: Maximum call stack size exceeded`，
   所以脚本把测试函数拆成多次调用。
8. **这个 build 会异步让出**（`gc.collect()` 等），`ccall` 必须带 `{async: true}` 并用 `await`。

## 局限

- 版本是 **MicroPython 1.17**（2022），比 ESP32 最新固件老。因此
  `encode("UTF-8")` 在这里**能用**（老行为是静默按 utf8 处理）——
  这恰好印证了"大写编码名"是个随固件版本变化的坑，不能靠它验证。
- 没有网络栈 → 无法验证 `socket` 层（那一层由 CPython 端到端测试覆盖）。
- **实跑边界**（实测结果）：整包 7 个模块都能注入成功、`probe()` 与 `test_flatten()`
  全部通过；但跑到 `test_vec3()` 时 Node 侧会报
  `RangeError: Maximum call stack size exceeded` —— 这是这个 wasm build 的 JS 侧栈太小
  导致的 harness 限制（`vec3.py` 本来就是上游未改动文件），不是移植代码的问题。
  栈参数（`mp_js_init` 的 128 KB）调过，再大反而更容易溢出。
- 结论只作参考，真机确认仍然应该跑一次 `mpremote run tests/test_mp_compat.py`。
