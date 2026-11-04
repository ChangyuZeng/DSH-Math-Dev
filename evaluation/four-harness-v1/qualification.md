# Mac 冷启动四臂资格结果

核对日期：2026-11-04（Asia/Shanghai）。本轮执行用户确认的 `four-harness-mac-cold-start-v2`：**Mac 优先、冷启动、外部信息严格隔离、模型预算统一、计算资源如实记录**。它修订了此前要求相同硬 CPU／内存额度的方案。比较对象是固定模型和信息条件下的完整 harness 表现；不能据此单独识别某个机制的因果收益。

**真实模型请求 0，正式运行 0。** 本轮没有连接或停止备用服务器的实验，没有调用模型求解 Ring15。共同计算与冷启动边界已有实际离线证据；C/R 的原生模型工具兼容仍未通过，四臂真实 smoke／正式启动保持关闭。

## 逐臂资格

| 臂 | 固定来源 | 实际证据 | 当前未通过的门槛 |
|---|---|---|---|
| C | Codex 0.166.0；`6ff1e1ca3b30fc8b914720681fcf5fe62e95f856` | 原生 CLI 已越过 managed-preferences 和代理错误，thread/turn 启动并到达网关；外部凭据及额外本机端口探针仍被拒绝 | 固定 Astra catalog 强制 code-mode-only，原生请求含尚未资格化的 `additional_tools` namespace；网关在派发前拒绝 `TOOL_NOT_ALLOWED`，0 次模型请求；共同工具往返、真实后代与压缩未资格化 |
| X | Xean 4.5.0；`26669ebb071f8fc13e8a51f66a474d7ce9af387a`；Pi 1.5.0 | 已核对 build6；原 public host、Explorer、Coordinator、六段验证、笔记重建：9 假请求、1080 fixture tokens、8 条原生父关系；五工具 smoke 夹具另通过 2 假请求／240 fixture tokens | fork/resume、并行后代、压缩、可选 Codex source-search 和原生状态产物约束未完整覆盖；真实提供方回执未验证 |
| R | 官方 frenzymath/Rethlas；`887cc46427636bbdd235160a112f9a30ae81d040` | 原 shell exec/resume、原 FastMCP memory/BM25/branch、原 verify API/subprocess、报告校验路径；基础扩展夹具 3 次假请求／19 次工具 | 真正 Codex 生成器／验证器遇到 C 相同的工具兼容门槛；显式子进程夹具不证明真实 Rust spawn、递归、repair、压缩或完整验证技能 |
| M | dsh-math 0.11.0；原 workflow 基线 `ffc582c52585d958bcb3198259ea27c69ee6f8bb` | 原 executeLoop/executeStep 和实际 Python context/store/service；精确 Note/Obligation 版本；五工具冷 smoke 夹具以 2 假请求完成工具往返；取消、未知恢复与目标工具拒绝通过 | 尚无已授权且经离线验证的真实输送器；提供方模型／effort／usage／输出约束、原生状态产物和完整冷启动执行配置仍待核验 |

R 使用官方上游，未替换成本地 Rethlas-V2 分叉。来源、角色与提示清单见 [源码审计](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/source-audit.md>)；逐项适配见 [适配差异](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/adapter-diff.md>)。源码审计保留取得源码时的历史运行时快照，后续实测以本文及当前冻结草案为准。

## 冷启动信息边界

正式模型材料只含统一题面与提交格式两个固定字节文件。材料清单不含公开基线证书、既有解法、检索链接、历史 run、旧笔记或路线提示。`prepare-cold` 建立四臂独立且全新的 home/workspace/state/cache/tmp；拒绝复用已有目录。它只准备输入，不启动研究，也不单凭空目录声称操作系统隔离已经合格。

smoke 使用另一套固定材料，只含 `2+3` 与答复要求。其 manifest、MCP 工具列表和网关执行入口仅提供材料、工作文件及 compute 五个工具；不声明、不执行 Ring15 check/submit。四臂原生机制工具须另行列举、计量并限制在当前 run，不得扩展为外部检索。

网关为正式／smoke scope 核对材料 ID／大小／SHA、CommonTools 类型及 run 身份、初始状态未使用、工具超时／产物配额，并自行绑定正确账本的强制计量回调；每次请求重查材料及绑定。共同文件接口使用目录描述符、O_NOFOLLOW 和硬链接检查，阻断跨 run 和宿主路径。外部浏览／搜索、旧会话／旧缓存读取关闭；本次 run 内自行产生的笔记和状态可以使用。

无法证明模型训练中从未见过题目；也未宣称控制了提供方内部缓存。这两项限制明确记录。公开基线复算与正式前沿核对只供操作者和独立评审使用，不放回模型材料。共享中间状态续跑及各臂自身续跑是未来独立设计，本轮不混入冷启动比较。

## Mac 计算和资源记录

Darwin 默认 `macos_monitored`：可信 Python bootstrap 在读取用户代码之前安装一次 Seatbelt；策略失败则停止，绝不退回宿主直接执行。文件／网络、fork/re-exec、工作目录写入等限制已用真实子进程验证。保留工具墙钟、CPU、文件／输出限制、RSS watchdog 和进程组取消清理作为宿主保护；不再要求 RLIMIT_AS 成功，也不把核心数或 RSS watchdog 说成硬内存保证。可选 strict 模式仍保持原有失败关闭语义。

共同工具记录 wait4 CPU、峰值 RSS、观测来源、已知小计和完整性；无法取得的值保留 null。即使 CPU 小计非空，只要子进程观察不全，完整性也保持 false。原生进程包装另外采样进程组／后代的 CPU、RSS、墙钟；采样可能漏掉短进程和瞬时峰值，CPU 时钟也有精度限制。它不与嵌套工具 CPU 相加，不伪造全树精确总量。清理使用已经观察到的祖先关系与进程出生身份，覆盖另建 session 的已知后代；正常退出、超时及 PID 重用回归已通过，未观察到的短命父进程仍是明确缺口。

统一的请求、token、墙钟、工具次数、单工具超时和产物预算仍保留；CPU 核数、累计 CPU 和内存不再作为公平预算或淘汰依据。配置中的保护阈值与观测缺口随运行记录。Linux／cgroup 不再是本方案的先决条件；本次没有启动 Docker daemon 或备用服务器。

## 计量与停止

- 所有角色／后代／压缩调用必须通过同一 run/campaign 的 SQLite 事务预留：正式每臂五个槽位，smoke 每臂一个槽位且至多 2 请求、总计 8 请求；失败不自动替补。
- Codex 原生 body 元数据与 header 投影须一致；MCP 传播 thread/turn，复用 child 的新 turn 可绑定新 parent turn。多物理请求对应同一 parent turn 时，缺少已证明的精确父 ID便拒绝，不猜测最近请求。
- 每个请求保留原始 usage、输入／缓存／输出／reasoning／total、请求身份、角色、父请求和 payload 哈希。缓存／reasoning 是子项，不重复累计。缺核心 usage 立即停止；未知成本保留预留或已知下界，不能计零或自动重放。
- 关闭与实际 transport.start 同锁排序；取消后的晚到 usage 可补记，不能重开 run、弱化已知成本或交付可提交的成功响应。当前没有真实 transport。
- 当前输入上界仍采用序列化 UTF-8 字节数加 4096 裕量；提供方 tokenizer／framing 与原生不透明上下文尚需资格化。实际 Codex 请求曾达到 52617 字节，连同裕量和 16384 输出预留为 73097，超过现有 65536 总上下文上限；没有为通过夹具而提高预算或删改 catalog。
- 原生 `additional_tools` 被明确拒绝，避免绕过顶层函数白名单。未覆盖的路径保留不合格状态，不静默换模型、改为自制循环或宣称账本覆盖全部原生行为。

## 离线验证

本轮共同 Mac 工具测试 41/41 通过，无跳过；冷启动／网关／账本新增回归覆盖：额外／篡改材料、预装状态、跨 run 绑定、计量回调替换、smoke 目标工具声明与直接调用、原生 additional_tools 旁路、CPU 小计不等于完整记录。独立只读审查复核了相关修复。实际进程采样测试也已通过。

本轮 `npm run build && npm test` 已通过：JavaScript 387/387；Python 317 项中 304 通过、13 个显式跳过。12 个可选原生夹具已在本轮单独运行；另一项为需显式材料路径的旧公开基线测试。共同 Mac 计算测试不再因 RLIMIT_AS 被跳过。原始日志、隔离构建与 fixture 回执保留在忽略目录，不提交到仓库。本次仅变更实验适配及测试，没有产品 UI／正常 runtime 更新，也没有 Desktop 安装或重启。

本轮 [M 冷 smoke 夹具](</Users/zcy/Codes/Math Agent/harness/dsh-math/dist/four-harness-mac-M-smoke-v2/report.json>) 已通过，scope 为 smoke、2 次假请求、0 次真实请求；另有 [Mac 进程观察回执](</Users/zcy/Codes/Math Agent/harness/dsh-math/dist/four-harness-mac-M-v2/report.json>)。X build6 的 8 项原生回归、M 的 23 项原生 workflow 回归均通过；R 原生 15 个用例分批通过。R 正向 MCP 曾被夹具自设的 25 秒客户端期限中断，改为经认证 manifest 的单工具期限（offline/formal 60 秒、smoke 10 秒）后重跑通过；整个扩展夹具限 75 秒。原服务器、beartype 与报告 schema 校验未关闭，冷导入开销保留。

此前的 [X host](</Users/zcy/Codes/Math Agent/harness/dsh-math/dist/four-harness-offline-X-host-final/report.json>)、[R MCP](</Users/zcy/Codes/Math Agent/harness/dsh-math/dist/four-harness-offline-R-mcp-final/report.json>) 和 [M workflow](</Users/zcy/Codes/Math Agent/harness/dsh-math/dist/four-harness-offline-M-final/report.json>) 保留作历史证据。这些都是确定性夹具，不是数学成绩，也不消耗真实 smoke 的授权额度。

此前仅重放 [公开基线 thread 274](https://einsteinarena.com/problems/ring-loading-15/threads/274)，独立整数枚举得到 `D=1048576, H=1179648, target_met=false`。未搜索目标、未用成绩挑题。该证书现在明确位于模型材料边界之外；开放状态与“中等难度”仍未确认。

## 真实运行前的门槛

真实 smoke 前还需：解决 C/R 固定 Astra 原生工具路径及上下文兼容；完成采用路径的后代／压缩／故障计量覆盖；验证四臂实际冷启动执行配置和原生状态产物约束；实现并离线验证无重试的真实 transport、解析与凭据边界；冻结确切版本、工具、提示和输入，然后取得用户单独授权。

正式 20 次运行前还需：smoke 通过；操作者侧 24 小时内前沿刷新和开放状态／难度核对；独立 checker 评审；两名盲审者与仲裁者；20 个固定槽位和顺序种子；冻结材料 SHA 与各臂提示／运行配置；再提交正式方案单独确认。共同题面材料已有可复算哈希，真实 resolved model、评审安排和顺序未知的字段保持未填。

## 非目标题 smoke 提案，尚未授权

任务仅为冷材料中的 `2+3`，要求原生 harness 调用共同材料或 compute 并消费结果。保留原生多轮决策；两次请求不足以走完整个多角色链就报告未完成，不追加、不替补。

| 项目 | 单臂上限 | 四臂总上限 |
|---|---:|---:|
| 所有角色的物理请求 | 2 | 8 |
| input+output tokens，含缓存计数 | 32768 | 131072 |
| 单请求输出 | 2048 | 每次相同 |
| 墙钟 | 180 秒 | 顺序执行 720 秒，teardown 单列 |
| 工具次数 | 8 | 32 |
| 单工具超时／产物 | 10 秒／16 MiB | 各 run 独立 |
| CPU／RSS | 观察并记录宿主保护阈值和缺口 | 不设相同公平额度、不汇总重叠观测 |

模型固定 `openai-codex / gpt-6-astra / max`，无 fallback。共同工具是材料、工作文件、计算五项；Ring15 checker／submission 不可用。成功要求原始请求与回执证明模型／effort／usage、工具结果进入后续原生请求以及总上限有效。

```bash
python3 -B evaluation/four-harness-v1/qualify.py smoke-plan
python3 -B evaluation/four-harness-v1/qualify.py launch --scope smoke --arm C --manifest <approved-frozen-manifest>
# X/R/M 同入口，共享同一个最多 8 请求 campaign。
```

当前 launch 一律返回 LIVE_NOT_AUTHORIZED；改文件权限或布尔字段不会启用模型。待工程门槛满足后，向用户展示可执行冻结版本并另行请求授权。本次修订授权不包含真实 smoke 或正式研究运行。
