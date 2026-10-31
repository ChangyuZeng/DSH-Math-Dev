# dsh-math 开发与交付约定

本 Git 仓库根目录就是插件目录；不要把上级 Math Agent 工作区、研究数据或 `dist/` 纳入仓库。在现有实现上迭代，不从零重写。

## GitHub 同步

- 用户于 2026-10-31 指定仓库 `https://github.com/ChangyuZeng/DSH-Math-Dev.git`，要求每次更新完成后提交并推送。此要求取代早期“不 git push”的限制；默认远端 `origin`、分支 `main`。
- 用户随后明确确认有权公开当前插件源码，包括 `runtime/p2c` 和 `contracts/cards`；授权范围与许可边界见 [PUBLICATION.md](<PUBLICATION.md>)。不要擅自添加开源许可证或执行 `npm publish`。
- 每次更新先检查工作区与远端差异，仅提交本次任务的明确变更。检查暂存文件清单及差异，排除凭据、研究数据、运行日志、审核包、安装配置和验收产物；不要盲目 `git add .` 或用 `git add -f` 绕过忽略规则。
- 通过相应验证后创建描述清晰的 commit，并执行普通 `git push origin main`。不得 force-push、改写历史或自动开 PR；远端分叉、权限不足、测试失败时停止推送并报告，不覆盖他人提交。
- 完成后报告 commit、分支和实际推送结果；以远端分支 hash 核对成功，不把本地 commit 当成已上传。

## 验证与安全边界

- 每次代码更新至少运行 `npm run build && npm test`。UI/运行时改动还须在隔离 math-dev 验证真实交互、无 Better Sidebar 回退及源码/包/安装文件哈希；不能把 hook 测试当作视觉或原生验收。
- 新导出及其搬移副本必须真正执行 `python3 -B verify_bundle.py .`；不得 monkeypatch 或改写旧审核包冒充通过。无研究授权时只用新的、无运行及无 program Review 的验收夹具。
- 保持精确 `ID@rev`、人工审核权限、`local_claim`、R1/R2 及导出边界；不把程序通过、过程图或模型自评称为全局证明。
- 不自动启动研究调用或 75 格计算；不修改原始审核文件、历史 run、研究数据、旧包或第三方插件源码。`modelRuns: []` 的验收配置保持关闭研究能力。
- 安装到 Desktop 仅限用户已授权的隔离配置，不擅自启用另一条 math 实例或修改其他插件；重启另行确认。

构建与产品使用说明见 [README.md](<README.md>)。历史许可检查记录保留在 [LICENSE-STATUS.md](<LICENSE-STATUS.md>)、[NOTICE](<NOTICE>) 与 [PROVENANCE.json](<PROVENANCE.json>)；当前指定仓库的公开上传确认以 [PUBLICATION.md](<PUBLICATION.md>) 为补充，不更改那些文件的历史内容。
