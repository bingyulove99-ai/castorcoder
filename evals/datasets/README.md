# 评测数据集

这里固定保存了两个公开的、按执行结果判分的代码基准数据集。两者都只由 `generation` 任务运行器
使用：每道题会在一个一次性工作区中展开，Agent 修改 `solution.py`，停止后再把隐藏测试复制进来。

| 文件 | 来源 | 行数 | 许可证 | SHA-256 |
|---|---|---:|---|---|
| `HumanEval.jsonl.gz` | [openai/human-eval](https://github.com/openai/human-eval) | 164 | MIT | `b796127e635a67f93fb35c04f4cb03cf06f38c8072ee7cee8833d7bee06979ef` |
| `sanitized-mbpp.json` | [google-research/mbpp](https://github.com/google-research/google-research/tree/master/mbpp) | 427 | CC-BY-4.0 | `ca95deaa9a01ef0a6f439f88bcf0dd3db3563d22f22aad6cae04ebb9a8d8c8e9` |

刷新或校验数据集：

```bash
python evals/scripts/fetch_datasets.py --check
python evals/scripts/fetch_datasets.py --force
```

方法说明：

- **HumanEval** 采用标准的零样本设置：提示、函数签名与文档字符串可见，测试隐藏。测试通过
  `python test_solution.py` 执行（官方的 `check(...)` 调用会在导入时运行）。
- **MBPP sanitized** 只展示题目描述与期望的函数签名，不展示断言。这样既避免了 MBPP 中常见的
  “照抄可见测试”捷径，又保留了明确的函数名。
- 数据集中的测试代码属于不可信的第三方代码。它在临时目录中执行，超时为 60 秒，且不保证网络
  隔离；不要在你并不信任这些数据集的机器上运行相关套件。
