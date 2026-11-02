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
- 每次版本验收通过后，默认更新本机 Desktop 的 `dsh-math` 到该版本并推送 GitHub（见下节），无需再次询问；更新只替换 `dsh-math`，不改 Desktop 其他插件、模型设置或研究数据。

## 浏览器使用

- 用户于 2026-11-02 要求避免插件优化时的空白测试 Chrome 干扰日常链接。手动预览通过系统默认浏览器打开，沿用用户平时的浏览器；不修改系统默认应用。
- 自动界面验收使用 Playwright 独立的无界面 Chromium Headless Shell，保留隔离 `math-dev` 与测试配置。缺少运行时可执行 `python3 -m playwright install chromium --only-shell`；不指定日常 Chrome 的 `executable_path`、`channel="chrome"` 或个人用户目录。
- 使用 `try/finally` 或等效资源管理保证验收成功、失败均关闭本次创建的浏览器。仅清理可核对来源的本次测试进程；现存 Harness 内置浏览器与用户浏览器单独诊断，不能按 Chrome 名称批量退出。

## Desktop 更新与 GitHub 同步

- 用户于 2026-10-31 授权：每个版本在隔离 `math-dev` 完成既定验收（`npm run build && npm test`、真实界面、无 Better Sidebar 回退、源码／tgz／实际安装文件哈希、新导出及搬移副本的 `verify_bundle.py`）后，即可更新本机 Desktop 并提交、推送 `origin/main`。
- 更新 Desktop 前先备份 `~/.dsh/profiles/desktop/package.json`、`pnpm-lock.yaml` 及旧安装版本证据到被忽略的 `dist/`；只改 `dsh-math` 依赖行，安装后用同一份清单核对源码、tgz 与实际安装文件的 SHA-256。
- 安装成功不等于已生效：Desktop 需重启才能加载新版。先完成当前回复并提示用户保存，不在对话中途强制退出应用；下次启动后核对插件页版本与数学研究入口，再报告“已生效”。
- 若安装或启动失败，按备份恢复依赖与锁文件并报告；不得用另一条 math 实例顶替，也不得为此修改第三方插件。
- 验收通过后按上节规则提交并推送；`Desktop 已安装`、`Desktop 已生效`、`已推送到 GitHub` 是三个独立事实，分别核对后再陈述。

构建与产品使用说明见 [README.md](<README.md>)。历史许可检查记录保留在 [LICENSE-STATUS.md](<LICENSE-STATUS.md>)、[NOTICE](<NOTICE>) 与 [PROVENANCE.json](<PROVENANCE.json>)；当前指定仓库的公开上传确认以 [PUBLICATION.md](<PUBLICATION.md>) 为补充，不更改那些文件的历史内容。
