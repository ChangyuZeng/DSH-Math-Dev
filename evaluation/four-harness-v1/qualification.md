# 四臂运行适配与资格记录

本轮沿用用户确认的 Mac 优先修订：固定 GPT-6 Astra / max、模型与工具预算、冷启动信息边界；计算资源如实观测，不宣称 CPU、RSS、操作系统或内部机制完全相同。比较对象是完整适配 harness。没有启动 daoshi-root，也没有修改日常模型配置、历史研究数据或生产插件。

正式题目仍为 Ring15，D=1048576、15 对整数、目标 H≥1179649，五次/臂、7200 秒/次、共二十次。开放状态与“中等难度”仍未确认。正式运行没有授权。

## 源码与实际入口

| 臂 | 固定版本 | 保留的机制与本轮离线证据 | 边界 |
|---|---|---|---|
| C | Codex 0.166.0；`6ff1e1ca3b30fc8b914720681fcf5fe62e95f856` | 实际安装的 Rust CLI、正常多轮循环、真实 MCP 计算结果进入下一次物理请求 | 工具元数据、不可用工具说明有明确适配；没有另写聊天循环 |
| X | Xean `26669ebb071f8fc13e8a51f66a474d7ce9af387a`；Pi 1.5.0 | 原始公开 host、Explorer/Coordinator、原生校验、原生分叉、长度续写、重建纠错；分叉 11 请求、长度续写 10 请求、纠错 11 请求均为假响应测试 | smoke 仍只有两次请求；不能把 Explorer 写出 Note 当成整个校验链 accepted |
| R | 官方 Rethlas `887cc46427636bbdd235160a112f9a30ae81d040` | 原始运行脚本、生成器、策略技能、记忆/BM25/分支、原始验证 MCP/API；真实 Codex 两轮计算夹具 | 原脚本达到 MAX_ITERATIONS 且没有 verified blueprint 时退出 1；单独用原始脚本哈希、真实 CLI 退出码、执行回执证明 bounded stop，绝不伪造通过标记 |
| M | dsh-math 0.11.0，核心基线 `ffc582c52585d958bcb3198259ea27c69ee6f8bb` | 实际 executeLoop/executeStep、Python 持久服务、Note/Obligation 精确引用；24 项原生工作流回归及同入口计算回路 | 没有把模型自评或程序计算升级为人工 Review 或全局证明 |

R 不使用本地 Rethlas-V2 fork 冒充官方版本。所有第三方原 checkout 保持不变，适配仅在隔离副本。X 的当前不可变 build、依赖文件树、C 原生二进制、Python/Node/Bun、R 的 69 个隔离依赖和 M 实际运行文件均由启动冻结清单重新核对，不能只相信版本字符串。

## 冷启动与共同接口

每个运行使用新的 HOME、CODEX_HOME、工作文件、工具状态、原生持久状态。模型只得到 canonical smoke 的 2+3 非目标题，或未来正式题干与提交结构；没有公开 baseline 证书、旧答案、既有 frontier、运行历史、其他臂状态。原生同一次运行的记忆允许保留。模型训练暴露和服务端缓存不能据此宣称消除。

共同接口为 materials.list/read、workspace.read/write、compute；正式范围额外开放 check/submit。smoke 不提供 Ring15 精确检查或候选提交。原生协调、技能读取、记忆与验证工具按白名单保留并计量，不给任意 shell、网络检索或任意源码读取能力。

计算在实际 macOS Seatbelt 下运行；网络、宿主私有文件、宿主控制目录均有真实拒绝测试。CPU、RSS、采样缺失及来源保留为观测，缺失不是 0。运行看门狗只清理经出生身份和祖先关系核对的本次进程；不宣称捕获了所有极短寿命后代，也不按进程名称清理宿主应用。

共同工具的模型回执统一精简：保留 ok/error，计算保留 status、returncode、stdout、stderr、output_bytes、output_truncated；CPU、RSS、runtime、资源保护细节保留在完整宿主审计。模型回执 result_sha256 对应实际发送的结果。`common_reply_projection` 精确绑定完整执行回执 SHA 与发送 SHA，C/R 原生 MCP 的墙钟前缀不影响实际结果验证。

## 请求、停止与计量

SQLite 在物理调用前完成全角色、全后代、全臂的事务预留。实际 transport.start 在写事务中再次核对 campaign 状态并立即发起发送，防止并行未知请求关闭后仍发送已预留请求。所有重试关闭；未知结果禁止自动重放，也不能换输出目录获得新的八次授权。

原始 usage 保留 input、cached input、output、reasoning、total 的语义；cached/reasoning 是子集。缺失和矛盾保留 null，已知下界不会被迟到或较弱回执覆盖。错误 HTTP、已收到 terminal 后 SSE 损坏、取消以及迟到回执均保留已知费用。失败状态不会转换成成功状态。

上下文第一次按完整 UTF-8 字节加 4096 framing 预留。仅当同一运行、同一角色、同一原生 thread/turn 的已完成父请求有完整实测 usage，固定输入字段完全一致、整个旧 input 前缀逐字节不变时，后续请求可使用实测旧前缀 input_tokens，加父 output_tokens、全部新增序列化 UTF-8、正向 metadata 差额与 4096 framing。新增 encrypted_content 的所有字节仍计入，并绑定原始提供方回执；没有选用猜测的 Astra tokenizer、截断上下文或抬高 65536 上限。分叉、压缩、缺失回执、字段变化或前缀变化均回到完整字节上界。每个估计方法、父回执和前缀/固定字段哈希均留痕；真实回执超界仍立即停止。

所有模型可写运行状态和完整请求/响应审计由 16 MiB 观察看门狗检查。原生运行目录不能访问宿主 common/.host、中央 ledger、认证文件或其他臂。临时文件原子重命名只允许有界整树重扫；扫描不明不能估计为 0。该观察看门狗不是文件系统硬配额或完整瞬时峰值证明。

## 已批准 smoke 与启动入口

用户已授权工程门槛通过后启动一次非目标题 smoke，并明确批准统一提高 token 上限。当前授权固定为：

| 项目 | 限制 |
|---|---|
| 模型 | `openai-codex/gpt-6-astra`，effort=`max`，没有 fallback |
| 物理请求 | 每臂最多 2 次，四臂合计最多 8 次，失败不补跑 |
| token | 每臂最多 131072，总计最多 524288，包含输入与输出，缓存输入不豁免 |
| 单次输出 / 上下文 | 2048 / 65536 |
| 时间 / 工具 | 每臂 180 秒，8 次工具，单工具 10 秒 |
| 运行状态与审计 | 每臂 16 MiB；资源实际观测 |
| 任务 | canonical 2+3，读取材料或实际计算并继续原生循环；不调用 Ring15 |

`smoke.py` 冻结当前源码、外部运行时实际字节、原生 build、冷输入和离线证据后，由 `qualify.py launch --scope smoke --arm all --manifest FROZEN --output FRESH` 启动同一个 campaign。宿主 transport 仅使用固定官方 Codex HTTPS endpoint，认证不传给模型子进程，禁止重定向、自动重试和自动刷新认证。TLS/认证预检不发送模型请求。

独立于输出目录的一次性授权标记阻止 crash、未知结果或更换目录后的重放。原始请求和回执只写被忽略的宿主验收目录；可提交源码中没有认证值或本次模型日志。`formal`、单臂独立启动和未冻结/变化的配置一律拒绝。

兼容性通过要求实际物理回执完整、工具真正执行、其精确结果进入下一次真实原生请求、运行状态与退出符合原生契约。算术结果必须从保留的原生 Note、proposal、报告或最终文本独立核对；不能由退出码 0、mock 的数字或 metadata 推断。两次请求不足以完成 X/R 全验证链时报告 incomplete，不增加调用。

## 本次真实 smoke 结果与离线修复

授权 campaign `authorized-smoke-m02454` 已执行并关闭，四臂均未通过。原始冻结清单、逐臂记录、usage 未知状态和一次性授权标记均保留在被忽略的 `dist/`，未重写、删除或补跑；宿主命令正常写出报告不等于 smoke 成功。

| 臂 | 确认实际 HTTP 请求 | 本次结果 | 后续仅离线修复 |
|---|---:|---|---|
| C | 0 | STATE_LINK，发送前停止 | 原生 Codex 的可选 PATH 别名创建 apply_patch/applypatch 符号链接；将精确别名目录设为 OS 只读，保留原生继续执行行为和全目录 StateGuard |
| X | 0 | TOOL_NOT_ALLOWED，原生 submit_notes/read_notes 声明被拒 | 宿主启动与夹具共同使用逐角色原生工具白名单；另关闭可选 Bun transpilation cache，避免快速临时重命名导致扫描不明，SQLite 状态继续计量 |
| R | 0 | STATE_LINK，发送前停止 | 同 C；原始运行脚本、策略技能和协调机制保留 |
| M | 1 | HTTP 400，没有任何 usage | 离线由 46 字节错误体的精确 SHA-256 匹配确认 Unsupported parameter: truncation；仅对 disabled 做明确的 native Codex framing 省略，并保留 max_output_tokens=2048 |

M 的未知请求保留 11430 token 预留量，total_tokens=null；这不是已知实际消耗 11430，更不是零费用。原始请求没有自动重放。错误体原始 SHA-256 为 `3ff2fba08b30fc4400c615241d0f7aae409846e6ab7cfd2ea6e84edc9bac5c73`。省略 disabled 的依据是已安装原生 Pi builder 不发送该字段，以及 OpenAI Responses 请求声明的默认 disabled 契约；这不证明真实后端已接受修复，也不证明指定模型、effort 或 2048 输出上限已经通过真实验证。

当前代码的修复不追溯改变本次冻结版本或失败结果。下一轮真实请求须另有明确授权与新的冻结清单，未知费用记录继续保留。当前四臂正式资格仍为不合格，二十次正式研究运行保持关闭。

## 验证与尚待事项

修复后完整 `npm run build && npm test` 已通过：JavaScript 388/388，Python 395 项中 366 通过、29 显式跳过。需要安装原生运行时的 opt-in 路径另行执行：C/R 全状态及宿主审计看门狗回归 10/10，X/M 共用宿主策略的同入口回归 10/10，StateGuard 6/6，transport 28/28。X/M 首轮回归曾被 Bun 可选缓存的快速临时重命名拦截，关闭该缓存后完整重跑通过；没有把失败跳过或换成假通过。所有这些修复后回归均为离线或假 HTTP，不是新增真实模型请求。

正式二十次实验仍须在 smoke 之后另行确认冻结方案：真实提供方模型/effort 与输出上限、四臂实际启动配置、统一冷输入、前沿刷新与材料冻结、随机顺序、两位独立匿名评审与第三仲裁者、独立精确检查，以及可审计的成本与失败口径。公开 baseline 的 9D/8 重放只属于宿主检查器验收，不能送入冷启动模型，也不证明题目当前开放或中等难度。
