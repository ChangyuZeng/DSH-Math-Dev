# 资格核对与接入契约

核对时间：2026-11-04（Asia/Shanghai），现有源码审计已推进至实际隔离适配与假模型联调。**真实模型请求 0；四臂仍未通过整臂资格。** 结果、失败证据、资源限制与待授权 smoke 详见 [qualification.md](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/qualification.md>)。

## 四臂现状

| 臂 | 固定版本与入口 | 已完成 | 整臂资格缺口 |
|---|---|---|---|
| C | Codex 0.166.0，`6ff1e1ca3b30fc8b914720681fcf5fe62e95f856`；隔离原生 exec＋共同 MCP | 固定 Astra/max；请求级网关；配置／凭据／网络隔离探针 | macOS managed-preferences 启动失败，0 次请求；原生工具兼容和压缩待资格 |
| X | Xean 4.5.0，`26669ebb071f8fc13e8a51f66a474d7ce9af387a`；Pi 1.5.0 | 原 public host 协调／验证／双笔记重建；9 次假请求、8 条原生因果父关系；修正 max→high 静默降级；工具计量和故障停止 | fork/resume、并行后代、压缩、可选 Codex source-search 及全树资源约束未通过 |
| R | 官方上游 `887cc46427636bbdd235160a112f9a30ae81d040` | 原 shell exec/resume；原 MCP memory/BM25/branch、共同工具、verify API/subprocess 和报告校验；扩展路径 3 次假请求／19 次工具；69 个依赖及字节固定 | 后代协议由显式夹具提供；真实 Codex、递归／repair／压缩／完整验证技能及全树资源仍未资格 |
| M | 插件 0.11.0，基线 `ffc582c52585d958bcb3198259ea27c69ee6f8bb` | 保留 executeLoop/executeStep 与实际持久 store/service；两请求共同工具往返；完整 usage／取消／恢复夹具 | 共同计算与全进程树硬资源限制、真实提供方语义未通过 |

DSH engine 0.2.1-rc.5、插件 0.11.0、模型名字、Codex 0.166.0、Node 24.21.0、Bun 1.4.2 和 Python 3.14.3 是不同版本域。二进制哈希与原生 prompt/config 来源记入冻结草案。

新增实现只在实验目录；X/R 在新导出副本上应用精确补丁。产品入口、原 checkout、历史数据和日常模型配置未修改。机制保留范围及适配差异见 [adapter-diff.md](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/adapter-diff.md>)。

## 共同 host 侧接口（离线实现与 live 契约）

```text
preflight(spec) -> {ready, blockers, source_pin, runtime_version,
                   resolved_model, capabilities, accounting_coverage}
run(spec, emit, AbortSignal) -> result
reconcile(run_id) -> result       # 只核对，不重新 dispatch
```

spec 包含不可变的 run/arm/repeat 身份、问题与材料哈希、源码提交/dirty 摘要、运行时版本、全部角色 model_policy、预算、工具清单与隔离策略、提示/配置/上下文策略哈希、输出位置。授权从宿主操作进入，不给普通数学聊天或 worker 自授权权限。

共同工具语义：

- `materials.list/read`：先返回受限目录，再按精确 ID/字节窗口读取；不暴露宿主文件系统。
- `workspace.read/write`：仅当前 run 沙箱路径，检查路径解析、符号链接、文件大小与总产物配额。
- `compute`：离线受限进程；相同 Python/数值库版本、CPU/内存/超时；不可把文本限制视作实际沙箱。
- `check`：冻结版本精确 checker，返回合法性、整数 H、target_met；CPU 与工具预算计入研究成本。
- `submit`：保存不可变候选、内容哈希、服务端时间和对应 run；独立 evaluator 再次复算，模型不能修改结果。

上述七个共同工具已由离线宿主实现，M/X 原生角色及 R 原 MCP 夹具已实际往返材料工具。R 扩展夹具也执行工作文件往返与 compute 资格探测。计算在本机严格资源预检失败时关闭，C 仍在首次请求前失败；不能据此声称四臂已具有同等可执行能力。

## 事件与计量

每条事件绑定 `event_id, run_id, seq, request_id?, parent_request_id?, thread_id?, role, utc_time, monotonic_elapsed`。

| 事件 | 必需内容 |
|---|---|
| request_reserved | 请求预算预留、输入上界、输出上限、冻结配置 |
| request_started | 请求的 model/provider/effort、宿主物理 request ID、prompt/context 哈希；提供方身份与 ID 尚未知时保持缺失 |
| request_finished | 提供方 response/request ID、返回的 model/effort（缺失不推断）、finish_reason、usage 原始响应与标准字段、计量来源 |
| request_unknown | 未完成或 usage 不可判定、保留预留、禁止自动重试 |
| tool_started/finished | 工具 ID、参数/结果哈希、wall/CPU/峰值内存、退出状态 |
| candidate_submitted | 内容地址、持久化回执与提交时间；不得用模型自报时间 |
| independent_check | checker 哈希、候选哈希、精确结果、检查耗时 |
| terminal | 成功触发/预算/主动停止/取消/技术失败、待核对事项 |

统一 usage：`input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, total_tokens, source, complete`，不可用 0 替换 null。提供方子项不能二次加到 total；所有协调器/Explorer/子代理/verifier/repair/压缩调用计入。未知 dispatched 请求继续占预算，核对不能自动再次请求。

墙钟包含启动到终止，数学成功时间另用最早有效提交；单次 teardown 超额也记录。成本报告必须保留实际超限观察，不能因超过上限把记录从数据集剔除。超限成功单列资格异常，不能混入“预算内成功”。

## 离线能力已交付与仍待资格

已交付：`audit.py` 从头枚举 Ring15 证书，含整数/长度/边界检查；无网络、模型或代码执行接口。测试使用独立位掩码 oracle 和 k=15 反例检查枚举语义。已只读复算题卡公开基线，结果 D=1048576、H=1179648、target_met=false，与已知 9/8 一致；这不是目标求解或新的数学成果。它只是检查器资格的一部分，仍需独立人员代码审核。

汇总工具只接收声明型结果，不读取事件账本验证身份；输出统一标 `reported`。`independent_verified=true` 不是签名；没有候选和审核回执就不能当正式成功。缺失/未运行槽位不算失败，样本未齐时不生成最终 x/5 成功率。已知 token 小计与完整总量分开，实际超限单独标记。

已新增来源固定、共同工具、物理请求计量与故障夹具。尚待：C/R 原生启动，X fork/resume、并行、压缩、Codex source-search 与 R 真实递归／repair／完整技能覆盖，全进程树硬资源资格，生产输送器与提供方解析，单独授权的非目标 smoke，专家/前沿/材料冻结及正式授权。**目前没有真实模型成功率、耗时对照或 token 收益数据。**

## 匿名评审与结果表模板

私有映射表保存 run_id ↔ anonymous_id ↔ arm/repeat；评审包只含精确题面、共同背景、候选及引用。两位审核者分别填写合法性、精确得分、定义映射、前沿/新颖性与可接受范围，随后独立仲裁分歧。模型不得填人工签名。

正式公开逐次表应包含：anonymous_id、解盲后 arm/repeat、版本、状态/停止原因、最佳 H、首次有效提交时间/token、全部 input/output/cache/reasoning/total、usage 完整性、工具 CPU/墙钟、超限、故障、审核结果与产物摘要哈希。未运行用 not_run，已运行未找到用 not_solved，基础设施失败用 infra_failure；原始失败永远保留。

汇总表每臂一行：成功 x/5 与精确区间、全部运行成本中位数/范围、成功条件下成本、计量完整率、工程失败数、最佳 H、获接受局部结论数；附逐次点图与预算曲线。四臂均零成功时明确写零，不把内部 verifier 通过率替换主指标。
