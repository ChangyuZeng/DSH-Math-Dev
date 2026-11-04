# 适配改动与原生机制边界

全部新增运行接口位于隔离实验目录；产品入口、历史研究数据、旧审核包、第三方原 checkout 和日常模型配置未修改。第三方适配由 [native.py](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/native.py>) 的 prepare_source 导出固定提交的 tracked 文件到新目录，再执行精确 before/after 替换；匹配次数、源码 dirty、提交、完整副本哈希或原生核心哈希不符即拒绝。构建回执和日志留在忽略目录。

## C：原生 CLI 包装

没有重写 Codex 的 turn loop，也没有修改 Codex 源码。包装创建新的 HOME、CODEX_HOME、XDG 目录，清除继承的凭据／proxy／loader，调用安装的 0.166.0 原生 CLI。实验配置固定 Astra/max、65536 上下文、49152 自动压缩阈值、HTTP Responses 网关、request/stream/unbounded retries=0；共同工具通过 stdio MCP 提供，并从宿主 manifest 取得当前 scope 的声明。关闭内置 shell、web、apps、plugins。隔离 Astra catalog 只把 tool_mode 从 code_mode_only 改为 direct、apply_patch_tool_type 从 freeform 改为 null；模型身份、max、上下文及原生 turn loop 不变。原生 plan 与共同 MCP 保留；移除不开放的资源发现/时钟声明。适配按原生 schema 精确校验并记录前后哈希，未知工具仍拒绝。

Mac 包装仅增加原生 CFPreferences 必需的两项精确 shared-memory 读取权限，以及本机网关地址的 NO_PROXY/no_proxy。普通文件、外部网络和其他本机端口仍被隔离。实际 CLI 已完成两次假 Responses 的真实共同计算往返。只在 apps/plugins 确实关闭时删去不可用说明；C 在所有内置技能关闭且原生可见目录为空时删去空技能目录的说明，R 的 AGENTS、技能目录和策略指令完整保留。完整原生通用行为、风格与推理指令没有任意截断，具体锚点、前后 SHA 及实际请求适配事件由桥接代码冻结。

## X：保留协调、笔记与验证定义，替换 I/O 边界

精确变更清单为 [xean.json](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/xean.json>)，新增边界为 [xean-native.ts](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/xean-native.ts>)。

| 位置 | 改动 | 保留／影响 |
|---|---|---|
| host transport retry/restart | maxRetries 8→0、restartLimit Infinity→0 | 避免丢失回执后的自动重发；这项适配差异纳入冻结 |
| role runtime recovery | maxRecoveries 8→0 | 未知请求由宿主核对后停止；不是无成本恢复 |
| 角色工具注册 | 原 defineTool 外包 meteredTool，按经认证的共同 manifest 注册工具（formal 七项、非目标 smoke 五项） | 原工具 execute、角色提交格式与 notebook 行为仍执行；原生状态工具也占工具预算 |
| 模型与 payload | 六个 native profile 均指向相同 Astra/max 和对应角色网关；显式 max→max；注入 thread ID 与输出上限 | 禁止 Pi 静默降级；上限由角色网关 manifest 给出 |
| 原 workflow、policy prompts/contracts、state、report、source runtime | SHA-256 与固定源码逐项比较 | 原生协调／验证定义保持一致；host/role-runtime 的已声明 I/O 适配单独记录，不能称其与上游逐字相同 |

基础两请求夹具运行原 Explorer createRoleRuntime＋Pi GenerationTask。新增 [完整 host 夹具](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/xean-host-fixture.ts>) 通过原 open、waitForIdle、inspect、exportAccepted，走通 Explorer→Coordinator→source/correctness/requirements→statement/blind-proof/comparison；9 次确定性假 Responses、8 条父关系、两条生成笔记的原生重建。父关系来自原生任务 ownership、工具回执和已提交 check 状态，没有自制协调循环。[复制及计量入口](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/x_full.py>) 同时固定依赖文件树和原生核心关系。原生 fork、空长度续写及 reconstruction 纠错现有额外假响应夹具；两请求真实 smoke 不启用 resume、并行后代、compaction 或外部 Codex source-search。原生状态及宿主完整回执受看门狗观察；CPU／RSS 改为带完整性说明的观测，不再以统一硬资源额度作为门槛。

## R：选择官方上游，保留 exec/resume 与递归策略

精确变更清单为 [rethlas.json](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/rethlas.json>)。root generator、verifier 和 subgoal-prover 显式固定 Astra/max 及角色 provider；不复用旧模型配置。原 shell 的三条启动／恢复路径保留，只把无沙箱参数改为 read-only，把在线检索改为共同冻结材料提示。原角色 AGENTS、生成 MCP 和递归策略源码保留；原 verifier endpoint 由原模块导入并执行。

[MCP 适配层](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/rethlas-mcp.py>) 导入原生成和验证 MCP 应用，仅加 FastMCP middleware、按当前 role manifest 注册的共同工具以及两处外部服务 transport：搜索只读共同冻结材料；verify 仍进入原 verifier API、原命令构建和 subprocess.run。原 memory、BM25、branch、报告校验／写入函数和 schema 保持原样。原生工具在执行前取得计量及精确父请求回执，verifier 子进程继承该父 ID；原 MCP 启动配置显式传入所有必要的角色令牌环境变量。原在线检索语义被限制为冻结材料匹配，这一适配差异保留在冻结清单。

[基础假子进程](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/rethlas-fixture-child.py>) 验证原 shell exec→resume；[扩展 MCP 夹具](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/rethlas-mcp-fixture.py>) 验证原 memory/branch/verify 的实际执行及共同工具，计入 3 次假请求和 19 次工具。扩展夹具显式提供后代协议元数据，不能作为真实 Rust spawn 证据。[69 个依赖的精确版本](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/rethlas-mcp-requirements.txt>) 和实际文件树一并固定。这些较早 fixture 不外推真实 Rust spawn。新增真实安装 Codex 的同入口 smoke 夹具走原始 shell 与两次物理请求，包含计算、共同错误回执和 encrypted reasoning 压力情形。原脚本在没有 verified blueprint 时达到 MAX_ITERATIONS 仍退出 1；宿主单独核对脚本/launcher 哈希、真实 CLI 退出 0、实际工具回执及确切停止标记，记录 bounded_stop_qualified，不改成整个验证链通过。原生技能只按冻结原始路径白名单读取并计量；模型/effort 菜单约束只展示统一 Astra/max，保留原有角色与协调能力。完整递归、repair、compaction 和验证链仍需另行资格。

## M：当前 workflow 的注入接口

[适配器](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/m_adapter.mjs>) 先校验生产 workflow runner 的固定哈希，再只在内存生成副本中替换一条 ACTION_SYSTEM 句子：

```diff
-No tool or network calls are available.
+Only the supplied benchmark tools are available. Use their common JSON contracts; network access is unavailable. After tool use return the required workflow proposal JSON.
```

原 executeLoop／executeStep 字节内容保持一致。注入的 prepareCall 在一个原生动作内处理工具调用并返回原 workflow proposal；每个物理请求分别计量。原动作继续依靠实际 context、store、service 的精确版本、持久事件和 commit 机制。适配器与 HTTP bridge 按已认证 scope 要求完整的五项 smoke／七项 formal 工具清单；实际执行也只接受该清单中的函数，未声明的目标检查／提交在调用工具前拒绝。

[隔离 Python 服务](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/m_service.py>) 继承现有 store 的转移／验证，仅扩展 benchmark action 的物理请求计数和完整 usage，并将“动作总输出”与“单物理请求输出上限”分开。接收失败／未知回执后保留已知成本，无 Note/Obligation 提交、无自动重放。人工 Review／授权权限保持原边界。

## 真实 smoke 后的离线修复

C/R 启动时会在 CODEX_HOME/tmp/arg0-macos 创建可选 apply_patch/applypatch 符号链接，与完整状态监督冲突。仅将这个精确目录设为 OS 只读；原生 CLI 按其既有降级路径继续，未放宽 StateGuard、状态配额或宿主读取权限。X 的宿主声明与 intrinsic 计量改用相同的逐角色白名单，保留原生 submit/read 工具；关闭可选 Bun transpilation cache，原生 SQLite 持久状态继续完整计量。

宿主 wire framing 仅省略明确为 disabled 的 truncation，记录省略前 SHA、语义与 wire 的分别 SHA；任何其他显式值在认证和发送前拒绝。依据为原生 Pi Codex builder 省略该字段、已安装 Responses 契约默认 disabled；max_output_tokens=2048 完整保留。错误诊断只输出请求字段静态白名单，不记录任意服务端文本。这些改动通过离线夹具，不追溯宣称失败 smoke 已通过，也没有发出新的真实请求。

## 所有臂共享

[ledger.py](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/ledger.py>)、[gateway.py](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/gateway.py>) 和 [tools.py](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/tools.py>) 提供共同模型计量、材料／工作文件／计算／检查／提交。原生机制工具不等于额外计算权限，必须列举、计数并受宿主边界约束。所有角色、请求和后代覆盖未通过前，不能把网关单元测试当成全臂预算保证。

[冷启动输入](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/cold_start.py>) 仅生成固定题面与提交格式，拒绝预装历史／基线／检索材料。正式 scope 提供七个共同工具；非目标 smoke 只提供五个，目标 check/submit 在声明和直接执行两处均拒绝。网关自行绑定当前 run 的计量，并核对未使用的初始状态。

Darwin 默认采用 monitored 计算模式；[计算 bootstrap](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/compute_child.py>) 在读取用户代码前安装一次 Seatbelt，策略失败即停止。CPU／输出／墙钟／RSS watchdog 是宿主保护，不能称为相同硬资源额度。原生包装通过 [进程观察器](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/observation.py>) 采样当前进程组和后代；缺失、精度和不完整性随结果记录，嵌套工具与进程观察不重复求和。

冻结草案记录每个适配源文件、精确补丁、native prompt/config 输入以及运行时二进制的 SHA-256。实际组装的请求另记 payload 哈希；动态端口、临时路径与能力令牌不充当稳定 prompt 身份。没有从 catalog 猜测真实提供方 resolved model。

所有臂的 smoke 共同工具回执使用同一个投影：计算正文保留状态、退出码、stdout/stderr、输出大小与截断标记；完整 runtime、CPU、RSS 和保护元数据留在宿主审计。发送的 result_sha256 对应实际发送正文，由中央投影事件绑定原始执行 SHA，不能把精简正文冒充完整正文。正常应用错误也保持完整 ok:false 回执与身份。

同一 thread/turn 的精确不变旧输入前缀可使用已完成父请求的实测 input usage，其余新增内容仍全部按 UTF-8 加父 output 与 framing 预留；opaque bytes 绑定原始提供方回执。没有推测 tokenizer 或删除原生 reasoning。实际发送与 campaign 停止由同一 SQLite 写事务顺序化；损坏 SSE、迟到或未知回执保留成本并禁止自动重放。一次性 smoke 授权不能通过新输出目录重置，正式运行入口仍关闭。
