# 适配改动与原生机制边界

全部新增运行接口位于隔离实验目录；产品入口、历史研究数据、旧审核包、第三方原 checkout 和日常模型配置未修改。第三方适配由 [native.py](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/native.py>) 的 prepare_source 导出固定提交的 tracked 文件到新目录，再执行精确 before/after 替换；匹配次数、源码 dirty、提交、完整副本哈希或原生核心哈希不符即拒绝。构建回执和日志留在忽略目录。

## C：原生 CLI 包装

没有重写 Codex 的 turn loop，也没有修改 Codex 源码。包装创建新的 HOME、CODEX_HOME、XDG 目录，清除继承的凭据／proxy／loader，调用安装的 0.166.0 原生 CLI。实验配置固定 Astra/max、65536 上下文、49152 自动压缩阈值、HTTP Responses 网关、request/stream/unbounded retries=0；共同工具通过 stdio MCP 提供，并从宿主 manifest 取得当前 scope 的声明。配置请求关闭内置 shell、web、apps、plugins，但固定 Astra catalog 实际仍会生成 code-mode-only 的 additional_tools；网关拒绝该未资格化路径，不能把配置开关当作已经执行的限制。

Mac 包装仅增加原生 CFPreferences 必需的两项精确 shared-memory 读取权限，以及本机网关地址的 NO_PROXY/no_proxy。普通文件、外部网络和其他本机端口仍被隔离。实际 CLI 现已启动并到达网关，随后以 TOOL_NOT_ALLOWED 在模型派发前停止。没有修改 catalog、替换模型或自制 Codex turn loop。code-mode 工具和保守上下文预留仍须独立资格化。

## X：保留协调、笔记与验证定义，替换 I/O 边界

精确变更清单为 [xean.json](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/xean.json>)，新增边界为 [xean-native.ts](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/xean-native.ts>)。

| 位置 | 改动 | 保留／影响 |
|---|---|---|
| host transport retry/restart | maxRetries 8→0、restartLimit Infinity→0 | 避免丢失回执后的自动重发；这项适配差异纳入冻结 |
| role runtime recovery | maxRecoveries 8→0 | 未知请求由宿主核对后停止；不是无成本恢复 |
| 角色工具注册 | 原 defineTool 外包 meteredTool，按经认证的共同 manifest 注册工具（formal 七项、非目标 smoke 五项） | 原工具 execute、角色提交格式与 notebook 行为仍执行；原生状态工具也占工具预算 |
| 模型与 payload | 六个 native profile 均指向相同 Astra/max 和对应角色网关；显式 max→max；注入 thread ID 与输出上限 | 禁止 Pi 静默降级；上限由角色网关 manifest 给出 |
| 原 workflow、policy prompts/contracts、state、report、source runtime | SHA-256 与固定源码逐项比较 | 原生协调／验证定义保持一致；host/role-runtime 的已声明 I/O 适配单独记录，不能称其与上游逐字相同 |

基础两请求夹具运行原 Explorer createRoleRuntime＋Pi GenerationTask。新增 [完整 host 夹具](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/xean-host-fixture.ts>) 通过原 open、waitForIdle、inspect、exportAccepted，走通 Explorer→Coordinator→source/correctness/requirements→statement/blind-proof/comparison；9 次确定性假 Responses、8 条父关系、两条生成笔记的原生重建。父关系来自原生任务 ownership、工具回执和已提交 check 状态，没有自制协调循环。[复制及计量入口](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/x_full.py>) 同时固定依赖文件树和原生核心关系。fork/resume、并行后代、compaction、可选 Codex source-search 与原生状态产物约束仍待资格；CPU／RSS 改为带完整性说明的观测，不再以统一硬资源额度作为门槛。

## R：选择官方上游，保留 exec/resume 与递归策略

精确变更清单为 [rethlas.json](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/rethlas.json>)。root generator、verifier 和 subgoal-prover 显式固定 Astra/max 及角色 provider；不复用旧模型配置。原 shell 的三条启动／恢复路径保留，只把无沙箱参数改为 read-only，把在线检索改为共同冻结材料提示。原角色 AGENTS、生成 MCP 和递归策略源码保留；原 verifier endpoint 由原模块导入并执行。

[MCP 适配层](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/rethlas-mcp.py>) 导入原生成和验证 MCP 应用，仅加 FastMCP middleware、按当前 role manifest 注册的共同工具以及两处外部服务 transport：搜索只读共同冻结材料；verify 仍进入原 verifier API、原命令构建和 subprocess.run。原 memory、BM25、branch、报告校验／写入函数和 schema 保持原样。原生工具在执行前取得计量及精确父请求回执，verifier 子进程继承该父 ID；原 MCP 启动配置显式传入所有必要的角色令牌环境变量。原在线检索语义被限制为冻结材料匹配，这一适配差异保留在冻结清单。

[基础假子进程](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/rethlas-fixture-child.py>) 验证原 shell exec→resume；[扩展 MCP 夹具](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/rethlas-mcp-fixture.py>) 验证原 memory/branch/verify 的实际执行及共同工具，计入 3 次假请求和 19 次工具。扩展夹具显式提供后代协议元数据，不能作为真实 Rust spawn 证据。[69 个依赖的精确版本](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/rethlas-mcp-requirements.txt>) 和实际文件树一并固定。所有 fixture 保持 native_qualified=false／live_ready=false；真实递归、repair、compaction、完整验证技能和原生状态产物约束仍待资格化。另有实际安装 Codex 的生成／验证启动诊断；它报告原生工具拒绝，不能用假子进程通过来替代该结论。

## M：当前 workflow 的注入接口

[适配器](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/m_adapter.mjs>) 先校验生产 workflow runner 的固定哈希，再只在内存生成副本中替换一条 ACTION_SYSTEM 句子：

```diff
-No tool or network calls are available.
+Only the supplied benchmark tools are available. Use their common JSON contracts; network access is unavailable. After tool use return the required workflow proposal JSON.
```

原 executeLoop／executeStep 字节内容保持一致。注入的 prepareCall 在一个原生动作内处理工具调用并返回原 workflow proposal；每个物理请求分别计量。原动作继续依靠实际 context、store、service 的精确版本、持久事件和 commit 机制。适配器与 HTTP bridge 按已认证 scope 要求完整的五项 smoke／七项 formal 工具清单；实际执行也只接受该清单中的函数，未声明的目标检查／提交在调用工具前拒绝。

[隔离 Python 服务](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/m_service.py>) 继承现有 store 的转移／验证，仅扩展 benchmark action 的物理请求计数和完整 usage，并将“动作总输出”与“单物理请求输出上限”分开。接收失败／未知回执后保留已知成本，无 Note/Obligation 提交、无自动重放。人工 Review／授权权限保持原边界。

## 所有臂共享

[ledger.py](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/ledger.py>)、[gateway.py](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/gateway.py>) 和 [tools.py](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/tools.py>) 提供共同模型计量、材料／工作文件／计算／检查／提交。原生机制工具不等于额外计算权限，必须列举、计数并受宿主边界约束。所有角色、请求和后代覆盖未通过前，不能把网关单元测试当成全臂预算保证。

[冷启动输入](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/cold_start.py>) 仅生成固定题面与提交格式，拒绝预装历史／基线／检索材料。正式 scope 提供七个共同工具；非目标 smoke 只提供五个，目标 check/submit 在声明和直接执行两处均拒绝。网关自行绑定当前 run 的计量，并核对未使用的初始状态。

Darwin 默认采用 monitored 计算模式；[计算 bootstrap](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/compute_child.py>) 在读取用户代码前安装一次 Seatbelt，策略失败即停止。CPU／输出／墙钟／RSS watchdog 是宿主保护，不能称为相同硬资源额度。原生包装通过 [进程观察器](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/observation.py>) 采样当前进程组和后代；缺失、精度和不完整性随结果记录，嵌套工具与进程观察不重复求和。

冻结草案记录每个适配源文件、精确补丁、native prompt/config 输入以及运行时二进制的 SHA-256。实际组装的请求另记 payload 哈希；动态端口、临时路径与能力令牌不充当稳定 prompt 身份。没有声称取得真实提供方 resolved model。
