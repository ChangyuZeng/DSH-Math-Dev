# 资格核对与接入契约

核对时间：2026-11-03，来源为本地源码只读检查。**四臂均未通过正式资格；本目录没有 live runner。** 未执行任何真实模型调用，表中源码定位不是已经验证的运行结果。

## 四臂现状

| 臂 | 可复用入口 | 已发现的具体缺口 | 资格 |
|---|---|---|---|
| C | 工作区 `harness/dsh-p3a/codex-entry.mjs`；`harness/dsh-ring15-preflight/codex.mjs` | 旧入口固定 gpt-6.1-sol/high；thread aggregate 不覆盖请求/重试；原受控工具 C 不能直接称 Codex plain。须新包装原生循环、隔离配置、核对模型档位和全量 transport 计量 | 未通过 |
| X | 当前工作区未定位到 Xean checkout | 历史报告引用 7e6e5fd，另有 4.1.0 描述，不能当当前提交。须取得固定版本并检查 Explorer 自身的模型/工具接口，不能拿另一 Codex worker 替代 Explorer | 源码待取得 |
| R | 工作区 `Rethlas/agents/generation/tests/run_example.sh` → `run_legacy.sh`；`agents/verification/api/server.py` | generator 默认 gpt-5.6-sol/max，verifier 默认 gpt-5.6-sol/xhigh；均需覆盖。JSONL 末尾累计 usage 与 verifier usage 文件未证明覆盖所有后代请求。原脚本含源码内数据写入与进程管理，不直接作为新基准入口 | 待适配与计量资格 |
| M | 本仓库 `workflow_runner.mjs` 的 executeStep/executeLoop，`host.mjs` 的人工授权入口 | 当前 0.11.0 明确拒绝非空 tools，向模型传 tools:[]；收到工具调用也拒绝。usage 仅保留 total/output，缺详细输入/缓存/reasoning 字段。须新增隔离 benchmark 接口，不能绕过现有人工授权 | 待工具与计量扩展 |

DSH engine 0.2.1-rc.5、插件 0.11.0、模型名字和 Codex CLI 版本是不同版本域。历史路径、目录名及最低版本要求都不能替代实际二进制版本。

本次未编辑 Xean/Rethlas、历史 runner 或当前插件运行接口；没有将四臂简化为自制 prompt loop。后续适配必须保留各 harness 核心行为，输出源码 diff，再决定是否仍符合臂定义。

## 共同 host 侧接口（设计契约，尚无 live 实现）

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

工具签名、返回窗口和错误语义须四臂一致；本节是开发契约，不能声称当前有这些模型工具。

## 事件与计量

每条事件绑定 `event_id, run_id, seq, request_id?, parent_request_id?, thread_id?, role, utc_time, monotonic_elapsed`。

| 事件 | 必需内容 |
|---|---|
| request_reserved | 请求预算预留、输入上界、输出上限、冻结配置 |
| request_started | 实际 model/provider/effort、提供方 request ID、prompt/context 哈希 |
| request_finished | finish_reason、usage 原始响应与标准字段、计量来源 |
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

尚待：取得/冻结 Xean，核对三种现有入口实际版本，开发共同工具/计量适配，离线 adapter 故障注入，已授权的非目标 smoke，专家/前沿/材料确认，以及正式授权。**目前没有模型成功率、耗时对照或 token 收益数据。**

## 匿名评审与结果表模板

私有映射表保存 run_id ↔ anonymous_id ↔ arm/repeat；评审包只含精确题面、共同背景、候选及引用。两位审核者分别填写合法性、精确得分、定义映射、前沿/新颖性与可接受范围，随后独立仲裁分歧。模型不得填人工签名。

正式公开逐次表应包含：anonymous_id、解盲后 arm/repeat、版本、状态/停止原因、最佳 H、首次有效提交时间/token、全部 input/output/cache/reasoning/total、usage 完整性、工具 CPU/墙钟、超限、故障、审核结果与产物摘要哈希。未运行用 not_run，已运行未找到用 not_solved，基础设施失败用 infra_failure；原始失败永远保留。

汇总表每臂一行：成功 x/5 与精确区间、全部运行成本中位数/范围、成功条件下成本、计量完整率、工程失败数、最佳 H、获接受局部结论数；附逐次点图与预算曲线。四臂均零成功时明确写零，不把内部 verifier 通过率替换主指标。
