# 四臂离线资格结果与后续启动门槛

核对日期：2026-11-04（Asia/Shanghai）。本次实现基于 `ffc582c52585d958bcb3198259ea27c69ee6f8bb`。**真实模型请求 0；正式运行 0；四臂目前均不具备真实 smoke／正式实验启动资格。** 本文区分已运行的离线机制测试与尚未通过的资格条件。所有假响应由本机确定性夹具产生，不是模型实验成绩。

## 逐臂资格

| 臂 | 固定来源 | 实际离线证据 | 未通过的门槛 | 当前结论 |
|---|---|---|---|---|
| C | OpenAI Codex CLI 0.166.0；源码 `6ff1e1ca3b30fc8b914720681fcf5fe62e95f856` | 新建 HOME/CODEX_HOME，原生 `codex exec`，共同 MCP 配置，禁重试；隔离金丝雀验证禁止读外部凭据及访问其他本机端口；版本检查通过 | 原生启动报 `Failed to synchronize managed preferences (code -32600)`，首次请求前停止；尚未证明 Astra 能在选定配置下暴露共同工具；压缩接口未资格化 | **不合格：0 次假请求；原生循环未跑通** |
| X | chaoxu/xean 4.5.0，`26669ebb071f8fc13e8a51f66a474d7ce9af387a`；Pi 1.5.0 | 原 public host→Pi 持久任务→Explorer→Coordinator→source/correctness/requirements→statement/blind-proof/comparison 跑通；9 次假 Responses、1080 tokens；8 条后续父关系由原生 ownership／工具回执／check 状态确认；全部 Astra/max | fork/resume、并行分支、压缩和可选 Codex source-search 尚未覆盖；共同计算与全进程树硬资源证明未通过 | **完整顺序流程离线通过；整臂不合格** |
| R | 官方 frenzymath/Rethlas，`887cc46427636bbdd235160a112f9a30ae81d040` | 原生成 shell exec/resume；原 FastMCP memory/BM25/branch、验证服务及报告校验／写入；共同材料和工作文件接入；扩展夹具记录 generator/strategy/verifier 三次假请求、360 tokens、19 次工具；未知父关系在原工具写入前拒绝 | 后代身份由明确的协议夹具提供，不能证明真实 Rust spawn；原生 Codex 仍受 C 的启动阻断；真实递归、repair、压缩、完整验证技能与全树资源预算尚未资格化；共同 compute 失败关闭 | **原 MCP／验证服务路径离线通过；整臂不合格** |
| M | dsh-math 0.11.0，基线 `ffc582c52585d958bcb3198259ea27c69ee6f8bb` | 原 executeLoop/executeStep、Python context/store/service 保留；实际持久 Note/Obligation 精确版本；两请求共同工具往返通过；动作内多请求、取消和未知恢复测试通过 | 共享计算的本机硬限制不合格；完整进程树预算和提供方回执语义未验证；尚无已授权的真实模型输送器 | **原生 workflow 离线路径通过；整臂不合格** |

R 没有选用本地 Rethlas-V2 分叉 `bfde0f717e9ccd1d70ad467d9db03b84c8edef44`；该分叉的每轮新会话与非递归子任务会改变比较对象。完整源码、许可证边界、角色和提示来源见 [source-audit.md](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/source-audit.md>)。该文保留取得源码时的运行时快照；随后安装的隔离 Bun/Python 依赖以本文和冻结草案为准。

## 已实现的共同边界

- 只读冻结材料、当前 run 工作文件、受控 Python 计算、精确 Ring15 checker、不可变候选提交。路径用目录描述符和 O_NOFOLLOW 检查，拒绝符号链接、硬链接、逃逸与配额突破。候选回执带服务端时间及内容哈希；程序通过不充当人工证明审核。
- 共同模型入口为本机能力令牌绑定的 Responses 网关；角色与 run 由宿主绑定。原生内置 shell/web 等不在允许的函数集合内。X 的原生提交／读取／协调工具和 R 的记忆／分支／验证工具属于保留的 harness 机制，单独显式列举并计入工具总数。原生状态文件的全树产物配额仍须由正式环境监督器补齐。
- SQLite 事务在发送前预留请求数及输入上界＋输出上限，同时检查单 run 和 campaign 预算。smoke 强制每臂一个槽位、至多 2 请求，campaign 至多 8 请求；正式模式每臂五个槽位，失败槽位不会自动替补。全部角色和后代共用计量域；已有 parent_request_id 必须属于同一 run。
- Codex 原生 body 元数据和 header 投影必须一致；MCP 传播原 thread/turn；同一原生 turn 的后续请求串联，复用 child 的新 turn 可绑定新的 parent turn。异步后代若对应多个可能的物理父请求，则要求已证明的精确父 ID，否则拒绝；不猜测“最近一次”。原生路径是否全部到达网关仍是逐臂资格门槛。
- 每个物理请求保留输入／缓存／输出／reasoning／总量、原始 usage、模型和 effort、角色、父请求、payload 哈希、停止状态。缓存与 reasoning 是子项，不二次加到 total。缺字段为 null；缺核心字段立即停止后续派发。详情缺失另标 detail_complete=false。
- 未知请求不自动重放，保留较大的预留或已观察成本下界，并阻断整个 campaign 的后续派发。崩溃核对只产生 unknown。晚到回执可补记成本，不能重开 run、降低已知计数或替换提供方请求身份。
- 关闭与实际 transport.start 在同一锁内排序；响应等待在锁外。取消后保留晚到 usage，拒绝向原生调用方交付可提交的成功响应。生产 transport 必须满足同步开始、Future 返回、无隐藏重试的契约；当前仅安装假 transport。
- 事件按序带前项哈希。该账本可检测链被修改；它不是外部签名，也不能证明未接入的原生路径已被计量。

## 本机计算限制的实测结果

macOS sandbox-exec 可强制文件／网络隔离，但严格计算子进程在启动资源限制时得到 `ValueError: current limit exceeds maximum limit`（RLIMIT_AS）。实现保持失败关闭，没有改成在宿主直接执行代码。另有独立进程监督测试验证进程组终止、超时、CPU 统计、输出限额和 RSS 观察，但这些测试不等同于硬内存／CPU 核数资格。

Linux bubblewrap、seccomp、只读根目录和 CPU affinity 路径已实现，**未在本机实际验证**；RLIMIT_AS 与单工具限制也不能替代整个 harness 进程树的 CPU、内存、产物和墙钟约束。原生嵌套工具的 CPU 观测还需与全树单一计数对账，避免父子观测重叠；当前文本输入预留采用序列化字节数加 4096 裕量，提供方 tokenizer／framing 和不透明上下文的实际边界尚须单独资格化。正式环境需要经验证的 Linux 容器／cgroup 或等价机制，完成全树资源约束与退出清理。检测到 Docker 客户端，但本机没有可连接 daemon；本次未启动或配置新 daemon。

## 离线验证覆盖

最终执行 `FOUR_HARNESS_NATIVE_FIXTURES=1 npm run build && npm test`：构建成功，JavaScript 383/383 通过；Python 293 项中 286 通过、7 项明确跳过。六项跳过来自本机严格 compute 隔离不可用，另一项是旧测试中的可选基线文件入口；本次公开基线已由新增独立夹具复算。C 测试验证了已知启动阻断的诚实报告，不表示 C 两请求循环通过。日志和验收产物不进入 Git。

统一 CLI 的最新证据：[X 原生 host](</Users/zcy/Codes/Math Agent/harness/dsh-math/dist/four-harness-offline-X-host-final/report.json>)（9 请求、1080 fixture tokens、8 父关系）、[R 原 MCP](</Users/zcy/Codes/Math Agent/harness/dsh-math/dist/four-harness-offline-R-mcp-final/report.json>)（3 请求、360 fixture tokens、19 工具）、[M workflow](</Users/zcy/Codes/Math Agent/harness/dsh-math/dist/four-harness-offline-M-final/report.json>)（2 请求、360 fixture tokens）。这些合计不受“真实 smoke 最多 8 请求”授权上限计数，因为全部为确定性假响应；真实模型请求始终为 0。

账本／网关／共同工具／原生包装／M workflow 的夹具覆盖：模型／effort 漂移；未知、缺失、矛盾 usage；并发预算预留；全部角色累计；请求／token／工具／CPU 耗尽；取消、断连、关闭竞态、晚到回执；崩溃恢复不重放；跨 run 父请求、材料／工作文件隔离；冻结来源变更；原生失败不得伪装成资格通过。

X 顺序 host 路径另验证原生笔记 n1/n2 的重建、usage 丢失停止、提供方失败停止、8 请求预算耗尽无 acceptance，及全部共同／原生工具计数。R 扩展路径固定 69 个 Python distribution、3652 个文件，原 MCP 函数与 schema 保持不变；搜索 transport 只访问共同冻结材料，不访问外部检索服务。两者的依赖文件树均记录 SHA-256。

共享账本／网关经过两轮独立只读审查；实际修复并回归验证了取消与派发竞态、晚到 usage 被弱回执覆盖、cached/reasoning 子项遗漏、child thread 复用，以及异步 child 被错误归给较晚父请求等问题。

X 集成实际抓到过 Pi 默认把 `max` 降成 `high`，网关在派发前拒绝。显式 `thinkingLevelMap.max = "max"` 修正后，角色夹具观测到正确请求。Codex 0.166.0 的请求结构不直接提供 max_output_tokens；网关显式记录并注入输出上限，真实提供方是否接受和兑现仍须 smoke 验证。

M 夹具使用实际 Python 服务，包含精确 N@rev/O@rev、动作内两次调用的汇总输出与单请求输出上限分离、失败响应中的有效 proposal 不得提交、HTTP 错误保留 server request ID 和 raw usage，以及未知动作不可重放。

公开基线只做重放：采用 [公开 thread 274](https://einsteinarena.com/problems/ring-loading-15/threads/274) 的十二对 1/16 整数骨架，乘 65536 并补三对零，独立枚举得到 `D=1048576, H=1179648, target_met=false`。没有搜索目标实例、没有目标试跑、没有依据成绩挑题。这次页面取得不构成正式运行前的 24 小时前沿刷新；开放状态与“中等难度”仍未确认。

## 启动前仍需完成

| 阶段 | 必须补齐的具体事项 |
|---|---|
| 真实非目标 smoke 前 | 修复 C/R 原生隔离启动并保持外部凭据／网络隔离；补齐 X fork/resume、并行后代、压缩、Codex source-search 及 R 真实递归／repair／完整验证技能的共同工具、usage／预算覆盖；验证整个进程树的 CPU／内存／产物／墙钟硬限制；实现并离线验证生产 transport、提供方解析和价格口径；重新冻结版本／提示／运行时／工具，向用户展示可执行的确切版本并取得单独授权；提供方实际模型、effort、usage 和输出限制由获准 smoke 进一步核验 |
| 正式 20 次运行前 | smoke 通过；题目开放状态／难度独立核对；24 小时内前沿刷新；共同材料清单和内容哈希；独立 checker 代码评审；两名盲审人及分歧仲裁人；固定顺序种子和 20 个槽位；凭据只由获准宿主注入；保留全部失败与超限；再向用户提交正式冻结方案并取得新授权 |

当前 [冻结草案](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/frozen.draft.json>) 的 execution_authorized=false，所有正式 gates 保持 false，材料哈希、提供方 resolved_version、评审人、顺序均未伪造。

## 待授权的非目标题 smoke 方案

任务：只读共同材料中的“2+3”，经共同工具取得结果并让原生 harness 消费该结果。原生循环决定第二次调用；不能为凑两次请求重写协调／验证逻辑。两次请求不足以覆盖多角色完整链时，报告未完成，停止，不追加或替补请求。不得向任何臂提供 Ring15 题面、证书或搜索提示。

| 项目 | 单臂上限 | 四臂合计上限 |
|---|---:|---:|
| 物理请求（含所有角色／后代） | 2 | 8 |
| input+output tokens，含缓存计数 | 32768 | 131072 |
| 单请求输出 | 2048 | 每个请求相同 |
| 墙钟 | 180 秒 | 顺序执行至多 720 秒，不含明确记录的 teardown |
| 工具次数 | 8 | 32 |
| CPU | 120 CPU 秒、2 核 | 480 CPU 秒 |
| 单工具超时／内存／产物 | 10 秒／4 GiB／16 MiB | 各 run 独立 |

模型必须为 `openai-codex / gpt-6-astra / max`，不允许 fallback。成功需要原始请求／回执证明模型与 effort、usage 完整、原生工具结果进入后续原生调用、全局上限有效。未知结果占用预算、立即停止，不重试。

计划入口（目前明确拒绝真实启动）：

```bash
python3 -B evaluation/four-harness-v1/qualify.py smoke-plan
python3 -B evaluation/four-harness-v1/qualify.py launch --scope smoke --arm C --manifest <approved-frozen-manifest>
# X、R、M 使用同一入口，仅替换 --arm；四臂共享同一个 8 请求 campaign。
```

即使手工把草案 gates 改成 true，当前入口仍返回 LIVE_NOT_AUTHORIZED；文件权限和配置布尔值不构成用户授权。这里展示的是待满足工程门槛后供用户单独确认的具体方案，本次没有执行。
