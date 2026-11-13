# 更新日志

本文件记录 CastorCoder 的用户可见变化。格式参考 Keep a Changelog，版本号遵循语义化版本。

## 未发布

- 新增 `use_project_config`（TOML）与 `CASTORCODER_USE_PROJECT_CONFIG`（环境变量）。全局
  `~/.castorcoder/config.toml` 可将其设为 `false`，让模型、API Key、Base URL 只保留在一份
  配置中，从任意工作目录启动都不再读取项目级配置。
- 文档补充全局安装方式（`pipx install .`、`uv tool install .`）与 PATH 配置方法，安装后可在
  任意终端运行 `castorcoder`。
- 新增 Claude Code 风格会话选择器：不带参数的 `/resume` 或 `castorcoder -r` 会列出已保存会话
  （时间、轮数、模型、摘要），用方向键移动并恢复选中项；`/resume <#>` 与 `/sessions` 的编号
  一致，恢复后回显最近几轮对话。非交互运行回退到最近一次会话。
- 流式回答改为按 Markdown 块渲染（标题、列表、代码围栏、表格），不再把 `##` 和反引号原样
  输出；工具回执压缩为一条按宽度对齐的紧凑行，回答、工具与推理块之间恰好保留一个空行。
- `_tolerant_stdio` 在 GBK/cp936 流无法编码渲染字符（例如 Markdown 项目符号）时自动切换为
  UTF-8。
- `/sessions`、`/tools`、`/status`、`/memory` 与 `/help` 改为对齐表格，不再输出零散文本或
  原始 JSON。
- 每轮工具调用上限从 50 提升到 200（经校验最高可设为 2000），新增 `--max-rounds N` 与运行时
  `/rounds [N]`；子 Agent 的上限从 20 提升到 50。长时间重构不再被旧上限截断。若已有
  `~/.castorcoder/config.toml` 仍写死 `max_rounds = 50`，该值优先，请调高或删除。
- JSONL 追踪改为默认写入 `.castorcoder/traces/`，可用 `--no-trace` 或 `trace = false` 关闭。
  若全局配置写了 `trace = false`，需要先修改才能在驾驶舱看到新运行。
- 无需离开对话即可启动驾驶舱：`/dashboard [port]` 在守护线程中启动并打开浏览器，追踪、会话、
  记忆与门禁默认使用当前工作区。
- 驾驶舱追踪瀑布流会汇总每次运行（事件数、总耗时、Token、工具调用/失败、停止原因），并显示
  每个事件的时间偏移、耗时和可折叠的原始载荷，而不是整页 JSON。
- 驾驶舱改为中文优先的全新界面：侧边栏导航、图标统计块、系统状态表、可过滤记忆库、Token 条形
  图与最近运行账本、发布门禁结论面板。标签页与单次追踪支持深链（`#traces`、`?trace=<run>`）。
- 追踪列表显示每次运行的开始时间、总耗时、事件数、Token、工具调用/失败与停止原因；
  `/api/status` 在用量账本为空时回退到最新追踪中的模型名。
- `/compact` 现在一定会有响应：在可能较慢的摘要调用前先输出“正在压缩”提示，支持
  `/compact [N]` 保留指定数量的最近消息，历史较短时也会执行并报告前后消息数与 Token 数，
  压缩后自动保存会话。单条命令失败不再终止 REPL，同时接受全角 `／` 与大小写混合的命令名。
- 新增 Claude Code 风格思考强度控制：`/effort`（不带参数时打开方向键选择器）与 `--effort` /
  `reasoning_effort = auto|off|low|medium|high|max`。适配层会把级别映射为 OpenAI 兼容接口的
  `reasoning_effort`（DeepSeek 接受 `none`/`low`/`high`/`max`；OpenAI 的 `max` 映射为
  `high`），以及 LiteLLM 下 Anthropic 模型的 `thinking.budget_tokens`。供应商拒绝该字段时会
  去掉后重试一次，并由 `/effort` 报告降级原因，不会让整轮对话失败。默认值为 `auto`，现有配置
  行为完全不变。
- `castorcoder gate` 现在真正支持 `--skip-tier2`（只跑单元测试）与 `--report PATH`（把门禁
  结论写到指定位置），此前这两个参数只被解析、未生效。
- 修复 `castorcoder graph fix` / `graph review`：CLI 之前把 LLM 与工作区直接传给期望 `Agent`
  的工作流构造函数，命令无法运行；现在会先构造带代码索引的 `Agent`，并传入正确的初始状态与
  结果字段。
- 项目文档统一为简体中文：`README.md`、`CHANGELOG.md`、`docs/ARCHITECTURE.md`、
  `docs/EVALS.md`、`docs/BENCHMARK.md` 与数据集说明全部改写为中文；命令、代码、路径与指标名
  保持原样。
- 测试：273 项通过，分支覆盖率 82%。新增覆盖方向键选择器、编号恢复、Markdown 块渲染与输出
  间距、轮次上限、后台驾驶舱命令、显式压缩、命令归一化、思考强度映射与降级、Graph 子命令
  与门禁参数，以及驾驶舱的追踪/用量摘要。

## 1.3.0 — 更快的循环、可见的工作、可恢复的会话

- 新增终端实时状态行（阶段、耗时、Token），每次工具调用输出一条紧凑回执；修复 CLI 观察器误读
  `event.name`（应为 `event.type`）导致事件渲染完全失效的问题。
- 推理展示新增 `brief`（默认，先显示一小段再隐藏）、`full` 与 `off` 三种模式，`/reasoning`
  可在运行时切换。Token 输出改为缓冲刷新，不再逐 Token 刷新。
- 替换非 ASCII 状态符号并让标准输出更宽容，修复 GBK/cp936 Windows 控制台运行中途抛出
  `UnicodeEncodeError` 的问题。
- 新增每轮自动保存会话、按工作区记录的恢复指针、`castorcoder -c`、任务板/模型恢复、`/new`、
  `/session`，以及按工作区隔离的 `/sessions` / `/resume`。
- 新增 `castorcoder.trim.shrink_seen`：在后续每次模型调用前缩短已读过的旧工具结果，最新一轮
  保持完整。
- 新增只读调用缓存（相同调用在发生写操作前直接复用）、相邻安全工具的并发分组，以及达到
  `max_rounds` 时让模型基于已有证据做一次不带工具的最终回答。
- 强化循环保护：跨轮重复签名、交替重放、连续多轮全部失败都会以明确原因停止；连续六轮只读时
  会给出一次纠正提示。
- 新增 `multi_edit`、`web_search`、`web_fetch`（带 SSRF 防护）与 `ask_user`；`edit_file`
  支持 `replace_all`、保留 CRLF，并增加容忍行尾空白的匹配与最近行诊断。
- 新增逐轮回执与持久化 `usage.jsonl` 账本，`/cost` 汇总近期运行。
- 事件流新增 `context.trimmed`、Token/推理计数、工具 `status`/`output_preview`，并在
  `run.completed` 中携带回执。
- `grep` 跳过垃圾目录、二进制与超大文件，支持上下文行，并在可用时使用 `ripgrep`；`glob`
  在遍历时剪枝；`read_file` 流式读取大文件并跳过二进制内容。
- `bash` 默认超时提升到 120 秒，只探测一次 Git Bash，把简单的 POSIX 读取翻译为更快的 `cmd`
  内建命令，仅在 cmdlet 或必须的 POSIX 命令上回退到 PowerShell。
- 显式指定 `--workspace` / `--config` 时不再继承进程目录的 `.env` 或项目配置；`CASTORCODER_HOME`
  可迁移用户状态，便于测试与便携安装。
- 测试：226 项通过，分支覆盖率 80.97%；新增覆盖裁剪、回执、网页防护、有界推理、会话隔离、
  调用缓存、最终回答、CRLF 编辑与快速路径翻译。
- v1.3.0 基准：HumanEval 164/164（pass@1 100%，95% CI 97.7–100%，1.988 轮，$0.400）；
  live 固定任务 7/7（10.0 轮、14.6 次工具、$0.203）；离线 8/8；仓库检索文件级 Recall@5
  hybrid 74.3% / BM25 80.0% / 哈希向量 40.0%，符号级 54.3% / 60.0% / 20.0%。
- 在更大仓库上发现旧权重让向量通道噪声过大，离线哈希嵌入器权重从 0.3 调整为 0.1。

## 1.2.0 — 基准适配器与隐藏校验

- 新增隐藏校验目录：评分测试只在 Agent 停止后才复制进工作区，模型无法修改自己将被评测的测试。
- 新增检索消融（`modes = hybrid | bm25 | vector`）、文件级或符号级标注（`gold_level`）、共享
  `queries_file`，以及用于避免基准自检索的 `exclude` 路径前缀。
- 新增 35 条查询的仓库检索基准与 CI 回归阈值。
- 评测报告新增尝试级 `pass@1`、各指标标准差与供应商上报费用。
- RRF 按嵌入器质量加权（`semantic_weight`）；离线哈希基线使用 0.1（v1.3.0 复测：纯向量
  Recall@5 40.0%、BM25 80.0%、hybrid 74.3%），因为它带来的互补召回有限。
- 系统提示词新增“验证通过即停止”的运行规则。
- 在 `docs/BENCHMARK.md` 与 `evals/results/` 中记录实测结果：21 次真实运行的 pass@1 85.7%，
  以及 35 条查询的检索消融。
- 测试扩展到 133 项，覆盖率 82.2%。
- 新增 `generation` 任务类型，并固定两个主流公开基准的适配器：HumanEval（164 题）与 MBPP
  sanitized（427 题）。每行数据在全新工作区运行，隐藏测试在 Agent 停止后应用；报告包含带
  Wilson 区间的 pass@1、失败分类、Token、费用与延迟分位数。
- 新增 `--jobs` 并行执行，以及对连接/API 失败的自动重试；基础设施错误单独报告。
- `LLMResponse` 现在携带供应商的 `finish_reason`（两个后端均支持）。生成类基准会把
  `max_tokens` 截断归类为 `truncated`，而不是错误地归因为 `no_edit`；`--ids` 可重跑指定
  数据集实例（用于 MBPP 输出上限消融）。
- 新增逐次尝试的 JSONL 检查点与 `--resume`：耗时数小时的生成基准可以中断后继续，恢复时传输
  失败会重试，而不是计为模型失败。
- 新增检索 k 值扫描（`ks = [1, 3, 5, 10, 20]`）与 `by_k` 报告。
- 新增 `evals/scripts/fetch_datasets.py`（SHA-256 固定下载）与
  `evals/scripts/validate_datasets.py`（标准答案预检）。

## 1.1.0 — 检索、记忆、MCP 与本地评测

- 新增工作区代码索引：AST/Markdown 感知分块、SQLite FTS5、确定性离线哈希向量、增量更新与
  RRF 融合。
- 新增 `search_code`、`/index`、`/search`、`--rebuild-index`，并把 `CodeIndex` 加入公开 API。
- 新增按工作区隔离的长期记忆：六类记忆、去重、置信度、TTL、替代关系、来源溯源、混合检索、
  运行后确定性沉淀与有界提示词注入。
- 新增 `search_memory`、`update_memory`、`/memory` 命令，并把 `MemoryStore`、`MemoryWriter`
  加入公开 API。
- 新增无第三方依赖的 MCP stdio 客户端、工具适配器，以及从 `[mcp.servers.*]` 配置加载的
  `--mcp`。
- 新增原子文件写入、变更回滚（`/undo`）与单工具超时。
- 新增本地评测框架（`castorcoder-eval` / `castorcoder.evals`）：脚本化运行时任务、
  检索 Recall@k/MRR/nDCG、记忆召回、可选真实模型任务，以及 JSON/Markdown 报告。
- 新增按供应商前缀查询价格、交互式审批回调，以及脱敏后的 MCP 配置输出。
- 测试扩展到 127 项，分支覆盖率 82%。

## 1.0.0 — CastorCoder

- 项目与主包从 Waku 重命名为 CastorCoder。
- 提供 `castor` 命令别名，方便旧脚本迁移。
- 用每个 Agent 独立的 `ToolRegistry` 替换全局执行查找。
- 新增工作区路径包含检查、权限模式、参数校验、敏感信息脱敏与依赖安全的工具并发。
- 新增类型化运行时事件、JSONL 追踪、运行指标、循环保护、取消与可选费用预算。
- 新增渐进式披露 Skills、任务板、角色化子 Agent 与项目指令。
- 新增分层 TOML/环境变量/CLI 配置、doctor 模式、状态命令，以及带旧格式发现的版本化原子会话。
- 自动化测试扩展到 74 项，并加入 Ruff、编译与打包 CI 检查。

## 0.3.0 — CastorCoder 上游基线

- 最小可用的 Claude Code 风格 Agent 循环、工具、上下文压缩、子 Agent、会话与 LiteLLM 支持。
