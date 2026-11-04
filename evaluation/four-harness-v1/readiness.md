# Mac 冷启动资格与接入契约

核对日期：2026-11-04（Asia/Shanghai）。采用用户确认的 `four-harness-mac-cold-start-v2`。**最新授权 m04168 已实际启动四臂，共 6 次 HTTP 请求，没有新增离线检查。X/M 各两次请求完成公共 compute→原生结果提交，分别计 3096/2557 tokens；C 在 SSE_UNKNOWN 后停止，R 收到 HTTP 400，两者 usage 未知且未重放。X 的协调/验证全链未在两请求预算内完成；四臂正式资格仍未通过。旧未知请求记录保持不变。** 当前目标是固定模型与信息边界的完整 harness 比较；CPU／RSS 如实记录，不再要求统一的硬 CPU／内存环境。具体实测与剩余门槛见 [资格结果](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/qualification.md>)。

## 四臂状态

| 臂 | 固定版本／原生入口 | 已完成 | 尚待资格 |
|---|---|---|---|
| C | Codex 0.166.0，`6ff1e1ca3b30fc8b914720681fcf5fe62e95f856` | 实际 Rust CLI 两轮共同计算；精确工具元数据适配、原生输入与执行回执校验、Mac 隔离修复 | 真正提供方兼容；压缩等未启用路径不能外推 |
| X | Xean 4.5.0，`26669ebb071f8fc13e8a51f66a474d7ce9af387a`；Pi 1.5.0 | 原 public host、校验、分叉、续写与重建纠错；共同计算、故障停止和父关系均有真实原生假响应夹具 | 两请求 smoke 不等于完整 accepted 校验；resume/并行/压缩和外部 source-search 不在该入口启用 |
| R | 官方上游 `887cc46427636bbdd235160a112f9a30ae81d040` | 原 shell、策略技能、memory/BM25/branch、verify API；实际 Codex 两轮计算、原生停止契约、opaque reasoning 压力夹具 | 两请求不证明完整递归/repair/验证链；真实提供方仍须 smoke |
| M | 插件 0.11.0，原 workflow 基线 `ffc582c52585d958bcb3198259ea27c69ee6f8bb` | 原 executeLoop/executeStep、实际 store/service、精确引用、共同计算与故障恢复；隔离状态监督 | 真实提供方与实际输出内容仍须 smoke |

DSH engine 0.2.1-rc.5、插件、原生 CLI、Node 24.21.0、Bun 1.4.2、Python 3.14.3 是不同版本域，分别记录版本及 SHA。新增逻辑局限于实验目录；第三方适配只写新导出副本，未修改原 checkout、产品入口、旧研究或日常模型配置。详见 [适配差异](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/adapter-diff.md>)。

## 冷启动与共同接口

```text
preflight(spec) -> {ready, blockers, source_pin, runtime_version,
                   resolved_model, capabilities, accounting_coverage}
run(spec, emit, AbortSignal) -> result
reconcile(run_id) -> result       # 只核对，不重新 dispatch
```

spec 固定 run/arm/repeat、模型政策、材料／提示／配置哈希、源码与运行时、请求／token／墙钟／工具／产物预算、工具白名单、隔离与资源记录策略。授权由宿主接收，模型不能自授权。

正式材料仅有题面和提交格式。四臂独立新建 home/workspace/state/cache/tmp；旧答案、公开基线证书、旧会话、旧笔记、浏览和外部检索均不进入模型环境。本次 run 自产笔记允许使用。提供方训练暴露与内部缓存无法验证，明确保留限制。未来中间状态续跑另设计，不混入本轮。

| 工具 | 接入语义 | scope |
|---|---|---|
| materials.list/read | 固定 ID／字节窗口；严格比对 canonical 内容哈希 | formal、smoke |
| workspace.read/write | 仅本次 run；拒绝路径逃逸、符号链接、硬链接及配额超限 | formal、smoke |
| compute | 离线 Seatbelt 子进程，先安装沙箱再读取代码；墙钟／输出／CPU／RSS 宿主保护，资源实测有缺口则标注 | formal、smoke |
| check | 冻结整数 checker，返回合法性、精确 H、target_met；独立评审仍须复算 | formal |
| submit | 不可变候选、内容哈希、服务端时间与 run 身份；不能伪造人工审核 | formal |

smoke 只用 `2+3` 材料；目标 checker／submission 不出现在声明，也不能绕过声明直接调用。Gateway 验证 CommonTools 的准确 run、未使用状态、超时／产物限制，自行绑定计量回调，每次请求重查材料和绑定。原生状态机制另列允许表并计入工具数，不能借此访问旧数据或外部服务。

## 事件、计量和观察

事件绑定 event_id、run_id、seq、可选 request_id／parent_request_id／thread_id、role、UTC 时间及 monotonic elapsed。

| 事件 | 内容 |
|---|---|
| request_reserved | 单 run 与 campaign 请求／token 预留、输入上界、输出上限、冻结政策 |
| request_started | 请求 model/provider/effort、宿主物理 ID、payload 哈希；不推断尚未知的提供方身份 |
| request_finished | 原始 response/request ID、实际返回 model/effort、finish 状态、raw usage、标准字段及来源 |
| request_unknown | 回执缺失或 usage 不可判定、成本下界／预留、禁止自动重放 |
| tool_started/finished | 工具身份、参数／结果哈希、wall、CPU／RSS 数值及来源／完整性、退出状态 |
| candidate_submitted | 候选哈希、不可变回执与服务端时间 |
| independent_check | checker／候选哈希、精确结果与检查耗时 |
| terminal | 成功、预算、主动停止、取消、基础设施失败或待核对项 |

统一 usage 字段为 input_tokens、cached_input_tokens、output_tokens、reasoning_tokens、total_tokens、source、complete。未知为 null，不计零。缓存／reasoning 是子项，不能重复相加。所有协调器、Explorer、后代、verifier、repair、压缩均须计量；无法接入的路径不能通过整臂资格。

原生 thread/turn 与物理父请求必须可核对；异步 child 若不能唯一归因则拒绝，不能猜最近请求。晚到回执可增加已知成本，不能降低、覆盖身份、重新开放 run 或触发重放。

CPU／RSS 是观测，不是本轮公平额度。工具 CPU 与采样进程树 CPU 不相加；部分数值不意味着完整记录。采样可能漏短进程及峰值，字段如实标注。墙钟仍是统一预算，含启动到终止；teardown 单列。超限、保护终止和技术失败均保留，不能从数据集中剔除。

## 独立评审与结果口径

[精确 checker](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/audit.py>) 枚举全部选择，测试含独立 oracle、整数／边界与 k=15 反例。此前公开基线复算 H=1179648 只验证操作者侧工具，既不是目标求解，也不进入冷启动材料。独立人员代码评审仍待完成。

汇总器只接受声明型结果并标 reported；independent_verified 布尔值不是签名。没有候选及审核回执就不能当正式成功。缺失或未运行槽位不算研究失败；样本未齐不生成最终 x/5。

私有映射保存 run_id ↔ anonymous_id ↔ arm/repeat。评审包仅含精确题面、候选、相应依据；两位审核者分别核对合法性、精确得分、定义映射、前沿／新颖性及结论边界，再由独立仲裁解决分歧，模型不得填人工签名。

逐次表保留版本、匿名身份、状态／停止原因、最佳 H、首次有效提交时间／token、全部 usage／完整性、工具与进程观察、宿主保护事件、技术失败、审核结果及产物哈希。状态区分 not_run、not_solved、infra_failure。每臂五次的成功率和区间、全部成本、成功条件下成本、计量完整率、失败数均须完整报告；四臂零成功时明确写零，不用内部 verifier 通过率替代。

当前已批准的非目标 smoke 固定每臂 2 请求、四臂最多 8 请求、每臂 131072/全局 524288 tokens，其余 2048 输出、65536 上下文、180 秒、8 工具/10 秒、16 MiB 均不增加。一次性授权独立于输出目录，工程门槛及实际字节冻结通过后由宿主启动；未知请求不重放，失败不补跑。正式 20 次另需 smoke 通过、24 小时操作者前沿刷新、开放性／难度核对、独立 checker 与评审安排、顺序／输入冻结，以及新的正式授权。**当前没有真实模型成功率、耗时对照或 token 收益数据。**
