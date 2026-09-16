// 用 MicroPython 的 wasm 构建（1.17）在 Node 里真跑一遍 mcpi 移植版。
//
//   node tools/wasm_micropython/run_tests.js [仓库根目录]
//
// 前置：把 @yeliulee/micropython-wasm 解压到本目录下的 package/（见 README.md）。
//
// 工作方式：这个 build 没有文件系统，所以不能"拷文件上板"。改为把每个 .py 的源码
// 交给 exec、模块对象注册进 sys.modules —— 源码里的相对 import（from .util import ...）
// 因此能正常解析，验证的仍是真实的包结构与 import 链。
const fs = require('fs');
const path = require('path');

const HERE = __dirname;
const LIB = path.join(HERE, 'package', 'lib');
const REPO = process.argv[2] ? path.resolve(process.argv[2]) : path.resolve(HERE, '..', '..');
const MODULES = ['util', 'vec3', 'block', 'entity', 'event', 'connection', 'minecraft'];

if (!fs.existsSync(LIB)) {
  console.error('找不到 ' + LIB + '，请先按 README.md 下载 @yeliulee/micropython-wasm 并解压到 package/');
  process.exit(2);
}

// Emscripten 在 Node 下用 fetch 取 .wasm，遇到相对路径会报 unknown scheme —— 拦下来读本地文件
const rf = globalThis.fetch;
globalThis.fetch = async function (url, opts) {
  const s = String(url);
  for (const c of [s, path.join(LIB, path.basename(s)), path.join(HERE, s)]) {
    try { return new Response(fs.readFileSync(c), { status: 200 }); } catch (e) { /* next */ }
  }
  return rf(url, opts);
};

const mod = require(path.join(LIB, 'micropython.js'));

async function main() {
  // 必须先 init；栈别开太大（JS 侧栈很小）
  mod.ccall('mp_js_init', 'null', ['number'], [128 * 1024]);
  const doStr = mod.cwrap('mp_js_do_str', 'number', ['string'], { async: true });

  const step = async (label, code) => {
    const r = await doStr(code.endsWith('\n') ? code + '\n' : code + '\n\n');
    console.log('[' + label + '] ret=' + r + (r === 0 ? '' : '  <-- 非 0 表示失败'));
    return r === 0;
  };

  await step('helpers', [
    'import sys',
    'class _M:',
    '    pass',
    'def _load(_name, _src):',
    '    _d = {}',
    '    _d["__name__"] = _name',
    '    _d["__package__"] = _name[:_name.rfind(".")]',
    '    exec(_src, _d)',                 // MicroPython 上 exec 必须给普通 dict
    '    _m = _M()',
    '    for _k in _d:',
    '        setattr(_m, _k, _d[_k])',
    '    sys.modules[_name] = _m',
    '    return _m',
    '_pkg = _M()',
    '_pkg.__name__ = "mcpi"',
    '_pkg.__package__ = "mcpi"',
    'sys.modules["mcpi"] = _pkg',
  ].join('\n'));

  for (const name of MODULES) {
    const src = fs.readFileSync(path.join(REPO, 'mcpi', name + '.py'), 'utf8');
    const ok = await step('load mcpi.' + name,
                          '_load("mcpi.' + name + '", ' + JSON.stringify(src) + ')');
    if (!ok) { console.log('!! ' + name + ' 注入失败，停止'); return 1; }
  }

  // 只跑函数定义，不跑结尾的 main()，再把各测试函数拆开逐次调用（绕开 JS 侧栈限制）
  let test = fs.readFileSync(path.join(REPO, 'tests', 'test_mp_compat.py'), 'utf8');
  test = test.replace(/\nmain\(\)\s*$/, '\n');

  console.log('========== MicroPython (wasm) 输出开始 ==========');
  await step('载入测试脚本', test);
  await step('环境探针', 'probe()');
  for (const fn of ['test_flatten', 'test_vec3', 'test_constants',
                    'test_events', 'test_minecraft_module']) {
    await step(fn, fn + '()');
  }
  await step('汇总', [
    'if FAILURES:',
    '    print("")',
    '    print("MPCOMPAT-FAILED", len(FAILURES), "/", CHECKS[0])',
    '    for _f in FAILURES:',
    '        print("  -", _f)',
    'else:',
    '    print("")',
    '    print("MPCOMPAT-OK:", CHECKS[0], "项断言全部通过 (MicroPython)")',
  ].join('\n'));
  console.log('========== MicroPython (wasm) 输出结束 ==========');
  return 0;
}

const boot = () => main().then(
  (code) => process.exit(code || 0),
  (e) => { console.error('runner 出错:', e && e.message); process.exit(1); });

if (mod.calledRun) boot(); else mod.onRuntimeInitialized = boot;
