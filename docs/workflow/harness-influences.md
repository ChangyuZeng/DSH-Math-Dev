# dsh-math 数学研究 harness：参考来源与设计取舍

本文记录 **dsh-math 0.11.0 的 workflow 和研究记录层**参考了什么、为什么，以及目前真正实现到哪里。这里的“固定模型”是指运行开始前冻结 provider、model 和推理强度；不是重新训练模型。实现基线为 `613b6bb61fa7a3b4385fe3e5a14cde0f458dcfc3`，文档核对日期为 2026-11-03。

## 先看结论

| 来源 | 在本项目中的地位 | 借鉴的核心 | 0.11.0 的真实状态 |
|---|---|---|---|
| Rethlas 和本地加固实验 | **直接设计输入**：项目早期计划明确提出复用经验 | 生成—检查—修订、区分错误与使用量、研究记录和恢复；不默认套用昂贵级联 | 已有单模型串行动作、有限预算、状态与回执；`check` 仍是模型意见，没有复现 Rethlas 的完整验证服务 |
| Xean | **直接设计输入**：项目早期计划明确提出借鉴笔记与依赖 | 结构化笔记与依赖、前提变化后重查、独立上下文、盲重建 | 有精确版本依赖与手动选择的盲重建；没有完整四阶段验证、Pi Durable、自动复用检查或 Xean 式自主 Coordinator |
| Danus / Pharos | 调研中的**概念参照**，不能证明逐项直接移植 | 研究结论、依赖、路线和验证状态的组织 | 有问题、笔记、义务和引用，以及状态投影；不是其内容寻址 fact graph 的复刻 |
| ProofCouncil | 调研中的**概念参照** | 把审查视角和审查过程显式化 | 可以手动发起 `check` 和独立人工审核；没有多模型议会、多方并行或其 DAG 编排 |
| Archon / Hilbert / Numina-Lean-Agent / Goedel-Architect / OpenProver 等形式化路线 | 调研中的**概念参照** | 将大证明拆为子目标并记录依赖；检查结论的精确范围 | 有 Obligation 与依赖，但 workflow worker 不能运行 Lean、程序检查器或检索；不存在自动形式化闭环 |
| Aletheia | 调研中的**相关比较**，没有证据证明本版直接复用其代码或提示词 | 生成、验证、修订的循环和自然语言验证的局限 | 本版有相似动作标签，但只是同一固定模型的显式串行调用，不等同其实现或能力 |
| ProofLoop（AI4Math） | 用户要求考察的来源，**尚无法核实** | 暂不归因 | 本次未能读取其原始目录，不能声称复现、采用其独有机制或比较性能 |

上述判断依据本地已有设计材料与当前代码；概念相似不自动等于实现沿袭或外部系统的独有贡献。以下把“为何参考”和“落到哪里”拆开说明。

## 1. Rethlas：把生成与检查组织成可追踪循环

**原先的问题。** 本地 Rethlas 阶段报告描述了自然语言证明生成与逐节验证；项目也观察到同模型生成与验证可能共享盲区，单一路线易过早收敛，运行错误和计量缺失会妨碍判断。项目早期 MVP 计划因此明确选择复用**错误分类、检查点、用量归因和局部验证经验**，而不整体复制重型编排。

**为什么这样做。** 把复杂问题切成可审查的动作，比只收一个完整答案更容易定位缺口、保存阶段成果和决定下一次工作；把格式、运行故障和数学问题区分开，避免将故障误报成反例或者证明失败。检查输出也应能追溯到目标与前提。

**当前落地。** 用户选择 `derive`、`check`、`revise`、`decompose` 或 `reconstruct`，也可显式启动预算内的串行循环；每轮独立上下文和模型请求，结果以结构化候选提交。父服务检查数据结构、问题范围、依赖版本和权限，再写 Note / Obligation。记录 usage；缺失不按零计算，中断未知请求不自动重放。见 [workflow_runner.mjs](../../workflow_runner.mjs)、[workflow_service.py](../../workflow_service.py)、[workflow_store.py](../../workflow_store.py)。

**边界。** `check` 是同一模型的一次检查，不是 Rethlas 原有严格验证服务，也不是数学证明。Rethlas 的历史实验证据不是 dsh-math 的模型对照结果。早期 15 题快速通道约 12/15 直接结束、token 下降 28.8%，而另一 14 题研究集仅 1/14 快速结束、token 增加 25.5%；任务、难度和记录不同，不能合并或推断本系统收益。因此首版不默认用模型置信度做级联门控，也不自动增加快速预审。

## 2. Xean：研究笔记、前提依赖与盲重建

项目本地 Xean 分析针对**旧提交 `7e6e5fd`**，记录其 Coordinator / Worker、结构化 notes、显式 support 依赖、分阶段验证及盲重建。本次另核对了 [Xean 仓库当前 README](https://github.com/chaoxu/xean/blob/26669ebb071f8fc13e8a51f66a474d7ce9af387a/README.md)：其列出 source、correctness、requirements 和 reconstruction 检查，使用 Pi Durable 管理执行与恢复；当前 README 随上游变化，不能将它等同于旧提交。

### 2.1 精确笔记与依赖

**借鉴点与原因。** 把论断和前提做成可引用的研究对象，而不是依赖一段越来越长的聊天：当某个前提变化时，可以定位受影响的论断并要求重查，保留论证过程和失败记录。重要条件不能为了适配上下文而悄悄丢失。

**当前落地。** 研究对象以 `ID@rev` 引用；构建上下文时获取精确依赖闭包，检查是否过期，控制输入大小，超限则拒绝调用而不是截断关键假设。前提变化产生 `needs_recheck`，不直接判数学命题为假。见 [workflow_context.py](../../workflow_context.py) 和 [research_store.py](../../runtime/p2c/research_store.py)。

**差异。** 这不是 Xean note 数据结构或其依赖传播的逐项复刻；跨项目自动复用未作为本版承诺。

### 2.2 盲重建

**借鉴点与原因。** 同模型直接阅读原证明再自查可能受原论证锚定；将证明正文和目标证据隐藏，只给精确陈述及允许的前提，让模型尝试重新构造论证，可能暴露证明跳步。它不消除模型共有盲区，也不能保证独立性。

**当前落地。** `reconstruct` 是可由用户明确选择的动作；上下文排除目标正文、其证据和目录信息，控制可能的泄漏。重建失败只能是 **inconclusive**，不能证明原命题错误。见 [workflow_context.py](../../workflow_context.py) 和 [workflow_runner.mjs](../../workflow_runner.mjs)。

**差异。** 它不会在通过检查后自动触发；串行循环的模型建议动作也不包含 `reconstruct`。没有实现 Xean 式 correctness → source → requirements → reconstruction 完整验证管线，更不能把一次成功重建称为独立形式证明。

### 2.3 可恢复运行与检查复用

Xean 旧版分析还启发了可恢复执行与已完成检查复用。当前使用 dsh-math 自己的事件、锁、CAS 和回执，不是 Pi Durable：先提交研究记录，再保存工作流回执；恢复只核对现有回执，不重放未知模型请求。精确条件匹配的验证复用只作为**建议**，目前不会自动跳过模型调用。见 [workflow_service.py](../../workflow_service.py) 和 [workflow_strategy.py](../../workflow_strategy.py)。

## 3. 其他系统：研究地图，而非声称完整复现

- **Danus / Pharos：研究事实及依赖。** 早期调研将“结论、依赖、研究路线与验证状态分开管理”列为值得参考的方向。当前数据层可以表达对象与引用，工作流展示路线和审核状态；它没有实现这些系统完整的事实图或多角色探索。这样设计的理由是让研究者能回答“结论依赖什么、哪些地方需要重查”。
- **ProofCouncil：多视角审查。** 调研强调显式审查过程与不同视角。当前只保留可见的同模型检查和独立人工审核入口；没有多供应商议会或多个并行 reviewer。这样做符合本项目先验证**同模型**编排增益的目标，也避免把多数模型同意等同于证明。
- **Archon、Hilbert、Numina-Lean-Agent、Goedel-Architect、OpenProver 等：子目标与形式验证。** 调研关注子目标分解、依赖和检查反馈。当前先使用可引用的 Obligation 表达缺口，并将程序结果、模型意见和人工接受分开；Lean 形式化、编译器反馈和自动 prover 尚未接入本 workflow。形式化能提供更强的特定命题检查，但题意形式化与工具链本身仍须核对。
- **Aletheia：生成—验证—修订。** [原论文 §2](https://arxiv.org/html/2602.10177v1)描述 Generator、自然语言 Verifier 和 Reviser 反复交互直至验证器认可或达到次数上限。本地综述把它作为相关参照，也强调自然语言 verifier 可能出错。当前流程在动作划分上相似；没有证据表明使用其模型、代码或提示词，不应把其报告成绩转移给本项目。

## 4. 工程底座与设计原则

**DeepSeek Harness（DSH）不是上述数学 agent，而是本项目的执行和产品底座。** dsh-math 使用宿主提供的模型服务、插件与界面能力；数学问题、证据、路线和运行状态由插件领域层管理。当前执行器每次进行一个无工具的模型调用，应用层不自动切换模型或重试；适配器内部行为与计量仍需单独核对。见 [host.mjs](../../host.mjs) 与 [workflow_runner.mjs](../../workflow_runner.mjs)。

该组合的待验证假设是：**在模型与资源条件相同时，以明确的待证事项、可追溯依赖和有针对性的检查组织推理，能提高可核查研究进展或可靠性。** 这是实验假设，不是已证明的性能增益。评测需比较直接求解、普通生成—检查循环和本 workflow，控制模型、预算和题集，并做独立盲评，详见[固定模型评测契约](../../contracts/workflow-evaluation-v1.md)。

## 5. 证据与来源说明

设计溯源以项目工作区内的以下材料为准；它们位于插件仓库之外，未随源码公开，路径供拥有本工作区的读者定位：

1. `report/math-harness-mvp-plan/README.md` 第 56–84 行：明确记录 Rethlas、Xean、事实图、多视角审查和形式化路线的取舍；该计划早于 DSH 方案，不能把其中提议都当作现有实现。
2. `report/math-harness-open-problem-plan/README.md` 第 55–84、91–95 行：记录转向 DSH、固定模型、暂缓 Lean、核心假设和已有方法重叠。
3. `report/Xean项目分析与启发.md` 第 138–178、219–241 行：旧版本笔记依赖、盲重建、持久化和验证复用的比较。
4. `report/Rethlas阶段性汇报.md` 第 41–82、119–140、269–296 行，及 `report/级联分流门控反事实分析报告.md`：历史实验、工程故障和门控局限。
5. `report/AI4Math数学智能体系统调研报告.md`：其他系统的二手综述，用作提出设计问题，不将其未经逐项核验的成绩或成本倍数视为本项目证据。

本次未能读取用户提供的 [AI4Math/ProofLoop 原始目录](https://github.com/onericecake/AI4Math/tree/main/ProofLoop)，因此不能确认它具体引用了什么。另有可访问的 [SuperMarioYL/proofloop](https://github.com/SuperMarioYL/proofloop/blob/19e4c4cd56a6758d84881d25f6b17a8f05c3fea2/README.md)：其 README 描述生成 Python / Lean 草稿，再用 Lean 检查器反馈驱动修订；尚未确认两者是否有关联。该项目自身亦指出 Lean 通过只覆盖 Lean 命题，不能自动证明与 Python 或自然语言需求等价；它的形式化修订循环目前未接入本 workflow。已访问的 [Xean README](https://github.com/chaoxu/xean/blob/26669ebb071f8fc13e8a51f66a474d7ce9af387a/README.md)、[Rethlas / Archon 论文](https://arxiv.org/html/2604.03789v2)与 [Aletheia 论文](https://arxiv.org/html/2602.10177v1)可核对所述机制；本项目设计溯源仍以本地计划与代码为准。本文件不将未经核实的外部综述当作性能或能力证明。
