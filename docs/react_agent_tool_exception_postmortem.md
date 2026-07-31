# ReAct Agent 工具异常复盘 — "generator didn't stop after throw()"

> 日期：2026-07-08
> 影响：`tests/test_react_agent.py::test_tool_exception`
> 状态：✅ 已根治（`span()` 已从 `@contextmanager` 重构为 `_SpanGuard` 类）

---

## 现象

工具内部抛 `ValueError("boom")`，Agent 在 `run()` 里 `except Exception as e` 捕获后写入 `observation`，但 `observation` 文本里看到的不是 `"boom"`，而是：

```
工具执行失败：RuntimeError: generator didn't stop after throw()
```

断言失败：

```python
assert "boom" in result.steps[0].observation
# E   assert 'boom' in "工具执行失败：RuntimeError: generator didn't stop after throw()"
```

---

## 排查走过的弯路

### 弯路 1：以为是 `_execute_tool` 改写了异常

第一反应是把 `_execute_tool` 从 `threading.Timer` 软超时改成纯同步调用，结果**报错一模一样**。说明：

- `tool_fn(**action_input)` 这一步异常类型/消息**确实是原样透传**的
- 问题在更外层，跟 `_execute_tool` 没关系

### 弯路 2：怀疑是 pytest 的 capture 机制替换了异常

看到 `RuntimeError: generator didn't stop after throw()` 是 pytest 报错的常见措辞，怀疑是 `capsys` / `capfd` 在捕获 stdout 时把异常吃掉又重抛。但代码里没有 `capsys`、没打 stdout、没 `print` 到 stderr，所以排除。

---

## 真正的 root cause

调用栈（关键路径）：

```python
# core/react_agent.py  run()
try:
    with span(f"tool:{parsed['action']}", ...):      # ① @contextmanager 包过，函数体有 yield
        observation = self._execute_tool(...)         # ② _boom() 抛 ValueError("boom")
except Exception as e:                                # ③ 实际抓到的是 RuntimeError，不是 ValueError
    observation = f"工具执行失败：{type(e).__name__}: {str(e)}"
```

为什么 `③` 抓到的是 `RuntimeError`？

1. `span()` 是 `@contextmanager` 装饰的 generator，**函数体里有 `yield`**。
2. `_execute_tool` 抛 `ValueError("boom")` → 沿 `yield` 抛进 `span` 的 generator 里。
3. `span` 的 finally 里调用 `langfuse_context.update_current_observation(...)`。
4. **关键**：`update_current_observation` 内部也是 `@contextmanager` 包过的 generator（langfuse SDK 的实现），形成了**嵌套 generator**。
5. 外层 generator 还没正常退出就被 `throw()`，CPython 检查到内层 generator 没正确 `close`，直接抛 `RuntimeError: generator didn't stop after throw()`。
6. **原始的 `ValueError("boom")` 在嵌套 generator 的异常传播过程中被吞掉**。
7. 外层 `except Exception` 抓到的是被改写后的 `RuntimeError`，`str(e)` 就是那段英文。

调用链可视化：

```
ValueError("boom")
  ↓ throw
span 的 yield（外层 generator）
  ↓ finally 触发
update_current_observation（内层 generator）
  ↓ 没正确 close
RuntimeError: generator didn't stop after throw()
  ↓ 冒到 run() 的 except
observation = "工具执行失败：RuntimeError: generator didn't stop after throw()"
```

---

## 修复

把 `except` 从 `with span(...)` **外层**挪到 `with span(...)` **内部**，让 `ValueError` 在 generator yield 内部就被消化，根本不传播到嵌套 generator。

`core/react_agent.py` 第 168 行附近：

```python
# 5. 执行工具
start = time.time()
observation = ""
try:
    with span(
        f"tool:{parsed['action']}",
        input_data=parsed["action_input"],
        metadata={"thought": parsed["thought"]},
    ):
        # 关键：异常必须在 span 的 yield 内部捕获，
        # 不能让 ValueError 穿过 @contextmanager 的 yield 抛出去——
        # 否则嵌套 generator（langfuse 的 update_current_observation 也是 contextmanager）
        # 会把异常改写成 "RuntimeError: generator didn't stop after throw()"，
        # 原始 "boom" 消息丢失。
        try:
            observation = self._execute_tool(
                parsed["action"], parsed["action_input"]
            )
        except Exception as e:
            logger.warning(f"Tool '{parsed['action']}' failed: {e}")
            observation = f"工具执行失败：{type(e).__name__}: {str(e)}"
            finished_reason = "tool_error"
except Exception:
    # span 本身（langfuse SDK）异常时静默吞掉，
    # 不影响 Agent 业务逻辑继续走
    if not observation:
        observation = "工具执行失败：未知错误"
        finished_reason = "tool_error"
```

修复后行为：

- `_boom()` 抛 `ValueError("boom")`
- 在 `span` 的 yield **内部**被 `except` 捕获
- 异常不会传播到嵌套 generator
- `str(e)` = `"boom"`，`type(e).__name__` = `"ValueError"`
- `observation = "工具执行失败：ValueError: boom"`
- 测试通过 ✅

---

## 经验教训

### 1. `@contextmanager` 的 yield 是异常传播的隐藏路径

`with span(...):` 看起来只是一个上下文块，但内部 `yield` 把 `with` 块变成了一个 generator 帧。**任何在 `with` 块里抛出的异常都会沿 yield 传播**，会被外层 generator 的 finally / 内层 generator 的 throw 影响。

**规则**：在 `with contextmanager(...):` 里调可能抛异常的业务代码时，**异常捕获也要写在 `with` 内部**，不要让异常穿过 yield。

### 2. 嵌套 generator + 异常 = 异常类型会被改写

CPython 在 `throw()` 进 generator 但 generator 没正确响应时，会用 `RuntimeError: generator didn't stop after throw()` 替换原始异常。**任何用了 `@contextmanager` 的库（langfuse / sqlalchemy / pytest capture 等）嵌套调用都要警惕**。

**防御模式**：

```python
# ✅ 推荐：捕获写在 with 内部
with observable_span(...):
    try:
        result = risky_call()
    except SpecificError as e:
        result = fallback(e)

# ❌ 反例：捕获写在 with 外部，异常会被嵌套 generator 改写
try:
    with observable_span(...):
        result = risky_call()      # 抛 ValueError("boom")
except Exception as e:              # 抓到的是 RuntimeError，不是 ValueError
    result = fallback(e)            # e.str() 是 "generator didn't stop after throw()"
```

### 3. 根治方案：把 `span()` 从 `@contextmanager` 改为普通类

**本案例的根因不是 `react_agent.py` 的代码写法，而是 `span()` 用了 `@contextmanager` 修饰的 generator。**

修复后，`span()` 返回 `_SpanGuard` 实例——一个实现了 `__enter__/__exit__` 协议的普通类，完全不涉及 generator。

```python
# core/observability.py
class _SpanGuard:
    def __enter__(self) -> "_SpanGuard":
        # 记录开始时间、调 langfuse API
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        # 记录结束时间、记异常信息、调 langfuse API
        return False  # 不吞异常，让异常原样传播

class _NoopSpanGuard:
    """Langfuse 不可用的占位"""
    def __enter__(self): return self
    def __exit__(self, *args): return False

def span(name, input_data=None, metadata=None):
    if not is_enabled():
        return _NoopSpanGuard()
    return _SpanGuard(name, input_data, metadata)
```

**核心区别**：
- `@contextmanager` → generator 函数，`yield` 处是异常传播的隐藏路径
- `__enter__/__exit__` → 普通方法调用，没有 yield，异常直接原样通过

**效果**：即使用户代码写成 `try: with span(...): risky() except ...`，异常也不会被嵌套 generator 改写。

### 4. 看到 `RuntimeError: generator didn't stop after throw()` 时的排查思路

按可能性排序：

1. **嵌套 `@contextmanager`**：`with a(): with b(): raise X` —— 最常见
2. **`pytest` 的 capture / `capsys`**：generator 函数里 yield 后抛异常，capsys 收不到
3. **`asyncio.run` + generator**：异步 generator 在事件循环关闭后抛异常
4. **`signal` handler**：信号处理函数里调 generator

排查方法：**逐步注释掉外层 `with`**，看 `e` 的类型是否变回 `ValueError`。本案例就是这么定位到 `with span(...)` 这一层的。

### 5. 调试技巧：打印 `type(e).__mro__` 和 `e.__cause__`

如果只看 `str(e)`，只能看到被改写后的英文。看到 `RuntimeError` 时应该立刻：

```python
except Exception as e:
    print(type(e), type(e).__mro__, e.__cause__, e.__context__)
```

- `e.__cause__`：raise X from Y 里的 Y
- `e.__context__`：except 块里 raise 新异常时，上下文里的原异常

本案例里 `e.__context__` 应该是原始的 `ValueError("boom")`，能直接定位。

---

## 相关文件

| 文件 | 改动 |
|---|---|
| `core/react_agent.py` | `run()` 第 168 行附近，`with span(...)` 内部增加 try/except |
| `core/observability.py` | ✅ 根治：`span()` 从 `@contextmanager` 重构为 `_SpanGuard` 类（`__enter__/__exit__` 协议），消除了 generator 异常改写问题 |
| `tests/test_react_agent.py` | 未改（用例一直是对的） |

---

## 后续可优化项

1. ~~**`span()` 改成普通函数 + try/finally**~~ ✅ **已完成！** `span()` 已重构为 `_SpanGuard` 类（`__enter__/__exit__` 协议），不再使用 `@contextmanager` generator。
2. **`update_current_observation` 加一层 catch**：在 `span` 的 finally 里包 try/except，吞掉 langfuse SDK 自己的异常。短期止血，长期还是得改 span。
3. **统一异常处理工具**：写一个 `@span_safe` 装饰器，自动在内部捕获异常并写 `observation`，业务代码无需关心。