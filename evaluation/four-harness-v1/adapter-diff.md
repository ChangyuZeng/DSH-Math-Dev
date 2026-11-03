# 适配改动与原生机制边界

全部新增运行接口位于隔离实验目录；产品入口、历史研究数据、旧审核包、第三方原 checkout 和日常模型配置未修改。第三方适配由 [native.py](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/native.py>) 的 prepare_source 导出固定提交的 tracked 文件到新目录，再执行精确 before/after 替换；匹配次数、源码 dirty、提交、完整副本哈希或原生核心哈希不符即拒绝。构建回执和日志留在忽略目录。

## C：原生 CLI 包装

没有重写 Codex 的 turn loop，也没有修改 Codex 源码。包装创建新的 HOME、CODEX_HOME、XDG 目录，清除继承的凭据／proxy／loader，调用安装的 0.166.0 原生 CLI。实验配置固定 Astra/max、65536 上下文、49152 自动压缩阈值、HTTP Responses 网关、request/stream retries=0；共同工具通过 stdio MCP 提供。内置 shell、web、apps、plugins 关闭，避免共同计算／材料预算被旁路。

这是“统一工具能力的 Codex plain”候选包装。Astra catalog 的 code-mode-only 元数据与此配置是否兼容尚未验证，原生 CLI 已在 macOS managed-preferences 阶段停止。不能把配置存在或隔离探针通过写成 C 资格通过。

## X：保留协调、笔记与验证定义，替换 I/O 边界

精确变更清单为 [xean.json](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/xean.json>)，新增边界为 [xean-native.ts](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/xean-native.ts>)。

| 位置 | 改动 | 保留／影响 |
|---|---|---|
| host transport retry/restart | maxRetries 8→0、restartLimit Infinity→0 | 避免丢失回执后的自动重发；这项适配差异纳入冻结 |
| role runtime recovery | maxRecoveries 8→0 | 未知请求由宿主核对后停止；不是无成本恢复 |
| 角色工具注册 | 原 defineTool 外包 meteredTool，附加七个共同工具 | 原工具 execute、角色提交格式与 notebook 行为仍执行；原生状态工具也占工具预算 |
| 模型与 payload | 六个 native profile 均指向相同 Astra/max 和对应角色网关；显式 max→max；注入 thread ID 与输出上限 | 禁止 Pi 静默降级；上限由角色网关 manifest 给出 |
| 原 workflow、policy prompts/contracts、state、report、source runtime | SHA-256 与固定源码逐项比较 | 原生协调／验证定义保持一致；host/role-runtime 的已声明 I/O 适配单独记录，不能称其与上游逐字相同 |

基础两请求夹具运行原 Explorer createRoleRuntime＋Pi GenerationTask。新增 [完整 host 夹具](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/xean-host-fixture.ts>) 通过原 open、waitForIdle、inspect、exportAccepted，走通 Explorer→Coordinator→source/correctness/requirements→statement/blind-proof/comparison；9 次确定性假 Responses、8 条父关系、两条生成笔记的原生重建。父关系来自原生任务 ownership、工具回执和已提交 check 状态，没有自制协调循环。[复制及计量入口](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/x_full.py>) 同时固定依赖文件树和原生核心关系。fork/resume、并行后代、compaction、可选 Codex source-search 和全树资源限制仍不合格。

## R：选择官方上游，保留 exec/resume 与递归策略

精确变更清单为 [rethlas.json](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/rethlas.json>)。root generator、verifier 和 subgoal-prover 显式固定 Astra/max 及角色 provider；不复用旧模型配置。原 shell 的三条启动／恢复路径保留，只把无沙箱参数改为 read-only，把在线检索改为共同冻结材料提示。原角色 AGENTS、生成 MCP 和递归策略源码保留；原 verifier endpoint 由原模块导入并执行。

[MCP 适配层](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/rethlas-mcp.py>) 导入原生成和验证 MCP 应用，仅加 FastMCP middleware、七个共同工具以及两处外部服务 transport：搜索只读共同冻结材料；verify 仍进入原 verifier API、原命令构建和 subprocess.run。原 memory、BM25、branch、报告校验／写入函数和 schema 保持原样。原生工具在执行前取得计量及精确父请求回执，verifier 子进程继承该父 ID；原 MCP 启动配置显式传入所有必要的角色令牌环境变量。原在线检索语义被限制为冻结材料匹配，这一适配差异保留在冻结清单。

[基础假子进程](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/rethlas-fixture-child.py>) 验证原 shell exec→resume；[扩展 MCP 夹具](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/rethlas-mcp-fixture.py>) 验证原 memory/branch/verify 的实际执行及共同工具，计入 3 次假请求和 19 次工具。扩展夹具显式提供后代协议元数据，不能作为真实 Rust spawn 证据。[69 个依赖的精确版本](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/patches/rethlas-mcp-requirements.txt>) 和实际文件树一并固定。所有 fixture 保持 native_qualified=false／live_ready=false；真实递归、repair、compaction、完整验证技能及全树资源限制仍待资格化。

## M：当前 workflow 的注入接口

[适配器](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/m_adapter.mjs>) 先校验生产 workflow runner 的固定哈希，再只在内存生成副本中替换一条 ACTION_SYSTEM 句子：

```diff
-No tool or network calls are available.
+Only the supplied benchmark tools are available: materials_list/read, workspace_read/write, compute, check, and submit. Use their common JSON contracts; network access is unavailable. After tool use return the required workflow proposal JSON.
```

原 executeLoop／executeStep 字节内容保持一致。注入的 prepareCall 在一个原生动作内处理工具调用并返回原 workflow proposal；每个物理请求分别计量。原动作继续依靠实际 context、store、service 的精确版本、持久事件和 commit 机制。

[隔离 Python 服务](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/m_service.py>) 继承现有 store 的转移／验证，仅扩展 benchmark action 的物理请求计数和完整 usage，并将“动作总输出”与“单物理请求输出上限”分开。接收失败／未知回执后保留已知成本，无 Note/Obligation 提交、无自动重放。人工 Review／授权权限保持原边界。

## 所有臂共享

[ledger.py](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/ledger.py>)、[gateway.py](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/gateway.py>) 和 [tools.py](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/tools.py>) 提供共同模型计量、材料／工作文件／计算／检查／提交。原生机制工具不等于额外计算权限，必须列举、计数并受宿主边界约束。所有角色、请求和后代覆盖未通过前，不能把网关单元测试当成全臂预算保证。

冻结草案记录每个适配源文件、精确补丁、native prompt/config 输入以及运行时二进制的 SHA-256。实际组装的请求另记 payload 哈希；动态端口、临时路径与能力令牌不充当稳定 prompt 身份。没有声称取得真实提供方 resolved model。
