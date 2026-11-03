# 四种 harness × GPT-6 Astra Max：单题开放构造评测

状态：**实验计划已批准；资格未齐、未启动真实模型调用**。本目录保存评测协议与离线辅助代码，不是可启动的四臂运行器。当前 dsh-math 0.11.0 不具备本实验所需的统一计算 worker；适配通过前不得报告为可运行。

## 1. 研究问题与范围

在同一模型、题目、材料、工具能力和资源上限下，比较 Codex plain、Xean、Rethlas 和 dsh-math 的独立成功率、时间、token、阶段进展与工程可靠性。主实验为**统一能力条件下的适配版**，不是四个产品开箱即用的比较。

用户已选：可精确检查的构造问题；每臂 5 次、每次 2 小时，20 次正式运行最多 40 墙钟运行小时。该实验是单题案例研究，不能推导一般数学能力排名；同题重复调用不是独立问题样本。

批准本计划不授权付费模型 smoke 或正式研究。实际启动前须单独展示冻结 manifest、适配差异、题目审核、费用口径与资格结果，再确认运行。

## 2. 首选任务：Ring15 严格下界改进

令 D=1048576。寻找有序整数对 (U_i,V_i)，i=1,…,15，满足 U_i,V_i≥0 且 U_i+V_i≤D。对于每个 Z_i∈{V_i,−U_i}，令 T=ΣZ_i、P_k=Σ_{i≤k}Z_i，并定义

H(U,V)=min_Z max_{1≤k≤15}|2P_k−T|。

**成功门槛：H≥1179649**。共同提供的已知基线为 H=1179648，即 9D/8；其复现不算新进展。所有变量和排列可变，不额外固定公开核心或对称性。

提交格式为 `{"pairs": [[U1,V1], …, [U15,V15]]}`；只接受 JSON 整数，不接受 bool、浮点、自动缩放或裁剪。独立枚举全部 32768 种选择，包括 k=15，计算整数 H。此目标只覆盖固定有限构造下界，不代表解决一般环路负载问题。

选择理由是证书小且可严格核验，**不是已经证实模型求解难度中等**。现有题卡来自工作区 `report/math-harness-open-problem-plan/p3-0/main-problem-card.md`，其状态为待专家确认。启动前 24 小时内重新核查 [固定参数榜单](https://einsteinarena.com/problems/ring-loading-15)、[公开基线](https://einsteinarena.com/problems/ring-loading-15/threads/274)及相关论文；保存来源与哈希。若已解决、定义冲突或不适合预算，暂停并重新冻结，不自动换题。Q7 的 305 边构造仅作重新选题的候选。

该题题面和公开基线已在本项目设计阶段出现，训练暴露未知，不能称作未见题。正式材料包须向四臂统一披露公开基线及路线警示，不能把未经重放的排除声明当定理。不在正式目标上用四臂试跑成绩挑题；可用非目标题和专家判断校准实验负担。预算内未找到不证明不存在；若出现全局排除证明，单独审核，不事后改写构造成功指标。

## 3. 四臂定义与公平边界

| 臂 | 保留的核心 | 禁止混淆 |
|---|---|---|
| C / Codex plain | 标准 agent 多轮工具循环和原生上下文管理 | 不是单次回答，不加自定义数学协调/验证链 |
| X / Xean-adapted | 协调、笔记依赖及原有验证机制 | 工具扩展和禁联网必须披露，不称原生 Xean |
| R / Rethlas-adapted | 原有策略、草稿、生成与验证服务 | 所有 verifier 使用相同模型，不以本地重写 loop 替代 |
| M / dsh-math-adapted | 待证事项、精确依赖、候选提交与持久状态 | 须补计算接口，不能拿历史专题 M runner 替代当前 workflow |

冻结每臂仓库提交、依赖版本、提示哈希、模型配置、适配 diff 和停止策略。角色提示与上下文策略不强行相同，因为它们是实验因素。所有外部角色、摘要和压缩的模型调用均须纳入同模型和计量约束。

共同能力：材料读取、工作文件读写、受限计算、证书提交与精确检查。不同包装不得改变返回信息；检查器 CPU 和调用计入研究工具预算。冻结材料包相同，包含题面、已知基线、来源及路线限制。正式运行断网，不能访问本地历史成果或其他臂；检索仅允许同一材料包。原生联网产品比较留作独立实验，不能与本实验合并。

## 4. 模型与资源配置

候选映射 `openai-codex / gpt-6-astra / max` 来自现有本地路由，并非四臂资格已通过。请求必须记录实际解析 provider/model/effort；禁止自动模型回退。若只能使用动态别名，披露不可解析的版本，集中交错执行并监测变化；可检测版本变化的区组隔离，不混算。

每次正式运行上限：

| 项目 | 上限 |
|---|---:|
| 墙钟 | 7200 秒 |
| 全部模型请求 | 120 |
| 输入+输出总 token，缓存输入仍计入 | 1000000 |
| 单请求输出 | 16384 token，待兼容预检 |
| 单请求上下文 | 65536 token 候选上限，待兼容预检 |
| 工具调用 | 500 |
| CPU/内存 | 2 核 / 4 GiB |
| 累计 CPU 时间 | 14400 秒 |
| 单次计算 | 60 秒 |
| 产物 | 1 GiB |

任一上限到达即停止；请求前保守预留、外部 watchdog 执行。不得以共同预算为由强迫早停系统用完额度。正式模型总上限为 20000000 token；费用需用当时可核实价格单独估计，订阅额度不强行换算美元。

模型 output/reasoning 上限语义和输入估算必须在预检确定；usage 缺失记 unknown，不填零、不以新 run 洗掉成本。所有失败、修订、摘要和压缩也计入。提供方已含 reasoning 的 output 不能再加一次 reasoning；cached input 是输入子项，不能抹除或重复累加。

## 5. 执行阶段

1. 离线资格：假流、已知答案的小构造、计量缺失、预算、取消、归档与权限检查，不调用模型。
2. 单独授权的模型 smoke：非目标题，每臂最多 2 次、全局最多 8 次请求，验证身份/档位/工具；不得按其求解表现调优正式提示。
3. 冻结：精确题目、最新前沿、两位审核者、材料/代码/提示哈希、预算、顺序、报告规则。
4. 正式：5 个区组，各 C/X/R/M 一次。前四组随机标签映射的平衡轮换，第五组预先随机排列；保存种子和实际顺序。默认串行避免争抢配额。
5. 每次新会话、新目录、新笔记和搜索状态；不可清除的提供方缓存如实记录。无人给数学提示，人工干预只用于平台故障与安全。
6. 公共提交入口给证书落盘时间和哈希。首次独立检查有效达标后统一停止；首次成功时间以当时提交的合格证书为准，不从最终答案倒推。
7. 基础设施失败保留在原样本中，不自动补跑。任何获准替补单列，原始失败与成本仍保留。

## 6. 指标与统计

主指标：每臂独立验证成功数 x/5、首次成功墙钟及 token、全部运行的实际成本和计量完整率。内部通过、模型自报、Note 数和 completed 均不是成功。

次指标：最佳合法 H、相对公开基线的差值、基线复现时间；独立审核的新局部结论；每角色输入/输出/cached/reasoning 用量；模型耗时、工具墙钟/CPU、等待和编排；上下文增长；重复搜索、约束误读与修订；API/工具故障、未知结果、停止原因；人工审核耗时。重叠时间不直接相加当作总 elapsed。

数学候选随机匿名编号，隐藏臂名和过程提示，两位独立评审先盲评，分歧由第三人仲裁；之后才查看过程日志解释行为。独立数学接受需核对整数证书、问题映射与公开前沿。内容可能透露风格，报告盲评局限。

每臂列出全部 5 次，给成功率及精确区间、时间/token 中位数与范围、逐次散点；成功条件下成本单列，不排除失败后宣称高效。画累计成功—时间/累计成功—token 和最佳 H—消耗曲线；未成功、早停和工程失败分别标注，不当作成功时间。单题5次不足以支持稳定显著性排名，不以调用数膨胀样本量。

四臂全失败则结论是预算内均未完成；比较可审核的阶段进展而非强行排名。行为相关性不证明机制因果，后续消融另行预注册。计量缺失足以改变排名时，成本结论不可判定。

## 7. 审计与交付

每个 run 保存独立事件：模型请求和实际配置、reservation、usage、错误、工具调用/耗时、候选/哈希、停止原因。授权记录、运行日志、完整提示和材料可能含私人内容，默认留在忽略目录；公开仓库只收协议、适配源代码和经核对的汇总。

[audit.py](audit.py) 是离线辅助工具：核对证书与汇总已提供的结果，不运行模型，不授予审核权限，不证明前沿状态或一般问题。可在仓库根执行 `python3 -B evaluation/four-harness-v1/audit.py check-witness <证书路径>` 或 `python3 -B evaluation/four-harness-v1/audit.py summarize <结果数组路径>`；输入结构见模块文档与 [离线测试](../../tests/test_four_harness_audit.py)。即使摘要中的独立验证字段为 true，仍须外部凭证支撑，JSON 声明不能替代签名/审核。

正式资格缺一不可：题目开放状态和映射确认、审核人落实、四臂同模型、统一工具、全量调用计量、预算可执行、独立检查器复核、跨臂隔离、版本冻结和真实运行授权。当前缺项见 [readiness.md](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/readiness.md>)；未通过前不开放 live runner。

## 8. 离线适配与可审计入口（2026-11-04）

已交付共同工具、SQLite 请求账本、能力绑定的本机 Responses/MCP 网关、四臂隔离包装与故障夹具。完整结论见 [qualification.md](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/qualification.md>)；逐项改动见 [adapter-diff.md](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/adapter-diff.md>)；来源与提示证据见 [source-audit.md](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/source-audit.md>)。

以下入口只生成本机假响应；输出目录必须尚不存在。X/R 需要已取得的固定源码、隔离依赖和副本，参数可通过 `--help` 查看；不会自动调用真实提供方或偷偷安装依赖。

```bash
python3 -B evaluation/four-harness-v1/qualify.py inspect
python3 -B evaluation/four-harness-v1/qualify.py fixture C --output dist/new-offline-C
python3 -B evaluation/four-harness-v1/qualify.py fixture X --output dist/new-offline-X
python3 -B evaluation/four-harness-v1/qualify.py fixture R --output dist/new-offline-R
python3 -B evaluation/four-harness-v1/qualify.py fixture X --coverage full --output dist/new-offline-X-host
python3 -B evaluation/four-harness-v1/qualify.py fixture R --coverage full --output dist/new-offline-R-mcp
python3 -B evaluation/four-harness-v1/qualify.py fixture M --output dist/new-offline-M
python3 -B evaluation/four-harness-v1/qualify.py freeze-draft --output dist/new-frozen-draft.json
python3 -B evaluation/four-harness-v1/qualify.py smoke-plan
```

C 当前预期返回非零和具体平台阻断；X/R 成功只表示报告中指定的部分原生路径通过，整臂资格仍为 false；M 两请求原生 workflow 通过不替代共同计算和全树资源资格。报告包含实际假请求数、回执、哈希、失败原因和通过条件，原始日志留在忽略目录。

[冻结草案](</Users/zcy/Codes/Math Agent/harness/dsh-math/evaluation/four-harness-v1/frozen.draft.json>) 固定正式预算（5 次／臂、7200 秒、120 请求、100 万 tokens 等）及已知来源；未知提供方版本、材料、评审和顺序字段保持未定。`launch --scope smoke|formal` 当前统一返回 LIVE_NOT_AUTHORIZED，配置编辑不能解除。先完成所列工程门槛并取得最多 2 次／臂、总计 8 次非目标 smoke 的明确授权；smoke 通过后再单独确认正式 20 次运行。
