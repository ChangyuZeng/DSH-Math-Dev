# 四种 harness × GPT-6 Astra Max：Mac 冷启动构造评测

状态：**2026-11-04 已批准采用 Mac 优先、冷启动、外部信息严格隔离、模型预算统一、计算资源如实记录的修订方案；未授权真实模型 smoke 或正式运行。** 协议修订为 `mac-cold-start-v2`，沿用当前目录及既有源码固定结果，不另起一套实验。

## 1. 研究问题与范围

比较 Codex plain、Xean、Rethlas 和当前 dsh-math workflow 在相同模型、最小题面信息、模型／墙钟／工具预算下的完整 harness 表现。保留各自协调、验证、策略、记忆和上下文机制。运行时、内部编排、CPU 和内存消耗可以不同；这些差异作为观测记录。结果属于整体 harness 的单题案例研究，不能分离出某一机制的因果收益，也不能推导一般数学能力排名。

首选当前 Mac。`daoshi-root` 仅为备用，本轮没有选择、连接、停止其原实验或启动新实验。未来共享中途状态的续研与本轮从零冷启动分开设计；同臂恢复自身 checkpoint 和四臂获得共同中间材料也是不同实验。

每臂 5 次，每次至多 7200 秒，共 20 个正式槽位，最多 40 墙钟运行小时。同题重复调用不是独立问题样本。协议批准、文件访问权限和配置布尔值均不授权真实模型执行。

## 2. 任务及信息边界

令 D=1048576。寻找有序整数对 (U_i,V_i)，i=1,…,15，满足 U_i,V_i≥0 且 U_i+V_i≤D。对于每个 Z_i∈{V_i,−U_i}，令 T=ΣZ_i、P_k=Σ_{i≤k}Z_i，并定义

H(U,V)=min_Z max_{1≤k≤15}|2P_k−T|。

成功门槛为 **H≥1179649**。所有坐标和排列可变，不额外固定核心或对称性。提交 `{"pairs": [[U1,V1], …, [U15,V15]]}`；只接受 JSON 整数，不接受 bool、浮点、缩放或裁剪。独立检查全部 32768 种选择，包括 k=15。目标只覆盖这个固定有限构造下界。

模型可见输入严格限制为 [冷启动模块](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/cold_start.py>) 生成的 `task.txt` 和 `submission.txt` 两份固定字节内容：定义、目标、提交规范与共同工具约定。不给公开基线证书、既有构造、路线警示、先前研究笔记、旧审核包、其他臂结果或外部搜索入口。材料目录多出文件、内容改变或存在链接时拒绝。模型仍可使用本次 run 自己产生的笔记、工作文件和内部记忆。

公开 H=1179648（9D/8）的证书只供操作者离线验证 checker 和事后比较，不进入模型材料、初始 prompt 或可读路径。以前取得的公开 thread 和源码审计也在模型工作区之外。训练期暴露与提供方缓存状态未知，不能声称模型从未见题。

开放状态与“中等难度”仍未确认。操作者须在正式运行前 24 小时内核查 [固定参数榜单](https://einsteinarena.com/problems/ring-loading-15)、[公开基线](https://einsteinarena.com/problems/ring-loading-15/threads/274)及相关来源，保存依据与哈希，但不向模型追加解题信息。发现已解决、定义冲突或不适合预算时暂停并重新冻结，不自动换题、不用四臂目标试跑成绩挑题。

## 3. 四臂与共同能力

| 臂 | 保留的核心 | 适配边界 |
|---|---|---|
| C / Codex plain | 标准多轮工具循环和原生上下文管理 | 不增加自定义数学协调／验证链 |
| X / Xean-adapted | 协调、笔记依赖及原有验证机制 | 替换外部 I/O、披露禁检索和计量适配 |
| R / Rethlas-adapted | 原策略、草稿、生成、递归与验证服务 | 全角色统一模型；不以自写 loop 取代 |
| M / dsh-math-adapted | 当前 workflow 待证事项、精确依赖、持久状态 | 注入共同计算接口，不使用历史专题 runner |

共同能力为材料读取、当前 run 工作文件读写、离线计算、精确检查及不可变候选提交。原生记忆／协调／验证工具可以保留，须显式列举并计数；它们不提供额外宿主文件或网络权限。外部检索禁用；R 的原搜索 transport 只能匹配当前两份材料，X 的任何 source-search 路径必须保持同样边界。

每次使用全新 HOME、缓存、工作文件、笔记和持久状态。只能访问当前 run 的允许路径；其他臂、旧 run、操作员材料和日常凭据均不可读。网络只允许数字 loopback 的受控网关，提供方连接由未来获准的宿主 transport 承担。版本、依赖、原生提示、配置、适配 diff 和停止策略分别固定。

## 4. 统一预算与资源观测

所有角色、后代、摘要、repair 和压缩调用必须使用 `openai-codex / gpt-6-astra / max`，无 fallback。提供方实际解析版本、effort 和 output 语义尚待获准 smoke 证明；别名与源码 catalog 支持不等于实际服务资格。

| 每次正式运行 | 上限 |
|---|---:|
| 墙钟 | 7200 秒 |
| 物理模型请求，所有角色累计 | 120 |
| input+output，缓存输入仍计入 | 1000000 tokens |
| 单请求输出 | 16384 tokens，待提供方核对 |
| 单请求上下文 | 65536 tokens，输入估算待资格化 |
| 工具调用，含原生工具及检查器 | 500 |
| 单工具墙钟 | 60 秒 |
| 工作产物 | 1 GiB |

正式模型总上限 20000000 tokens。请求前事务预留；未知请求不重放、不记零、不换 run 洗掉成本。cached 和 reasoning 是子项，不重复加总。失败和超限的真实成本保留；费用以届时可核实价格单独估算。

**CPU 核数、累计 CPU、内存不再是跨臂统一公平预算。** Mac 计算使用文件／网络隔离、墙钟、输出和取消约束；工具 CPU 上限、RSS watchdog 等属于宿主保护，记录具体阈值、触发和局限。默认 4 GiB RSS 阈值是监测后终止，不能宣称硬驻留内存限额。未测得的 CPU/RSS 为 null；采样可能漏掉短命进程与峰值。整进程组采样和嵌套工具 CPU 不相加冒充全树总量。保留可选 strict 模式作为独立能力，不再因 Mac 不支持旧 RLIMIT_AS 而宣称必须迁移 Linux。

## 5. 阶段、审核与报告

1. 离线资格：真实原生入口＋确定性假响应；计算、冷信息隔离、usage、预算、取消、崩溃与跨运行检查。无真实模型调用。
2. 单独授权的非目标 smoke：每臂至多 2 次、总计 8 次；验证身份、档位和工具往返。不能据其表现调优正式数学提示；两次不足以走完原生链时如实记未完成。
3. 正式冻结：精确题面、操作者前沿核查、独立 checker 审阅、两位盲审人和仲裁人、版本／提示／材料哈希、预算及执行顺序，再单独批准。
4. 正式运行：5 个区组，各 C/X/R/M 一次；前四组随机标签映射的平衡轮换，第五组预先随机排列。记录种子与实际顺序；默认串行。失败槽位保留，不自动补跑。
5. 公共提交入口记录服务端落盘时间、run 与证书哈希；首次独立检查有效达标后停止。内部 verifier、模型自评、Note 数或 completed 不作为成功。

主指标为每臂独立验证成功数 x/5、首次成功墙钟和 tokens、全运行成本与计量完整率。次指标含最佳合法 H、操作者计算的相对公开基线差值、独立接受的局部结论、各角色 usage、工具耗时、CPU/RSS 观测范围、上下文、故障和停止原因。未成功不证明不存在，超限成功单列。

全部 5 次逐项呈现，成功率给精确区间；成功条件下成本单列，失败不剔除。单题 5 次不支持稳定显著性排名。四臂全失败时明确为预算内均未完成。数学候选匿名、两位评审独立判断、第三人仲裁；模型不能填人工签名。缺失计量足以影响比较时，成本结论不可判定。

## 6. 审计与离线入口

结果见 [资格表](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/qualification.md>)；具体接入契约见 [readiness](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/readiness.md>)；机制差异见 [适配 diff](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/adapter-diff.md>)；固定来源见 [源码审计](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/source-audit.md>)。旧严格资源方案的结果保留在 Git 历史，当前资格以本修订和最新测试为准。

下列入口不调用真实提供方。输出目录须全新；源码与隔离依赖必须已固定，不自动安装或获取模型凭据。

```bash
python3 -B evaluation/four-harness-v1/qualify.py inspect
python3 -B evaluation/four-harness-v1/qualify.py prepare-cold --scope formal --output dist/new-cold-formal
python3 -B evaluation/four-harness-v1/qualify.py prepare-cold --scope smoke --output dist/new-cold-smoke
python3 -B evaluation/four-harness-v1/qualify.py fixture C --output dist/new-offline-C
python3 -B evaluation/four-harness-v1/qualify.py fixture X --coverage full --output dist/new-offline-X-host
python3 -B evaluation/four-harness-v1/qualify.py fixture R --coverage full --output dist/new-offline-R-mcp
python3 -B evaluation/four-harness-v1/qualify.py fixture R --coverage native --output dist/new-offline-R-codex
python3 -B evaluation/four-harness-v1/qualify.py fixture M --output dist/new-offline-M
python3 -B evaluation/four-harness-v1/qualify.py fixture M --tool-scope smoke --output dist/new-offline-M-smoke
python3 -B evaluation/four-harness-v1/qualify.py freeze-draft --output dist/new-frozen-draft.json
python3 -B evaluation/four-harness-v1/qualify.py smoke-plan
```

R 的 `--coverage full` 使用原 MCP／验证机制与显式假子进程；`--coverage native` 启动实际 Codex 的原生成／验证命令，仍只连接假网关。后者若工具兼容性未通过，报告不合格并退出 2；不能把前者的通过当作实际 Codex 路径通过。

prepare-cold 只生成题面、哈希和空状态，不证明 OS 隔离已通过，也不运行研究。正式／smoke 网关额外核对材料清单及字节；离线故障夹具可以使用明确标注的合成材料。[独立 checker](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/audit.py>) 和 [测试](</Users/zcy/Codes/Math Agent/harness/dsh-math/tests/test_four_harness_audit.py>) 只核验给定证书，不搜索目标或授予人工审核。

[冻结草案](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/frozen.draft.json>) 中模型 resolved version、评审人及顺序仍未伪造，execution_authorized=false。`launch --scope smoke|formal` 始终返回 LIVE_NOT_AUTHORIZED。完成工程门槛后须再次展示具体可执行版本，等待真实 smoke 的明确授权；通过后再确认正式 20 次运行。
