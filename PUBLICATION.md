# GitHub 公开同步范围

## 2026-10-31 用户确认

项目用户指定 `https://github.com/ChangyuZeng/DSH-Math-Dev.git` 作为后续开发仓库，要求每次更新完成验证后提交并推送。首次推送前已检查该仓库为 PUBLIC、无现有提交，并向用户说明现有许可记录与包内本地原型、问题卡的范围。

用户通过确认选项明确表示有权公开当前插件源码（含 `runtime/p2c` 本地原型及 `contracts/cards` 问题卡），要求继续推送。本文件记录该用户声明及操作授权，不是独立的法律权属鉴定。

这一确认补充 [LICENSE-STATUS.md](<LICENSE-STATUS.md>)、[NOTICE](<NOTICE>)、[PROVENANCE.json](<PROVENANCE.json>) 和 [README.md](<README.md>) 中较早的“公开分发待确认”记录：针对上述指定仓库的上传，用户确认步骤现已完成。原文件原样保留，以免改写已验收的 0.5.0 包文件和来源记录。

## 不改变的边界

- 没有选择或添加开源许可证。`private: true`、`license: UNLICENSED` 和来源/依赖通知保持不变；公开可读不等于授予任意再分发或再许可权。
- 未获授权进行 npm 发布、修改第三方插件源码或发布研究数据。科学审核、研究执行授权与源码公开是三个独立事项。
- 上传范围为插件运行源码、构建所需文件、当前自动测试和必要开发文档。首次基线是 0.5.0；此轮新增 Git 忽略规则及开发/公开同步说明，并让常规测试脱离仓库外的历史材料，不改原有 55 个包文件。
- 不上传 `dist/`、研究数据、历史 run、原始 R1/R2、审核包、运行日志、截图、测试安装 profile、账号配置、凭据、依赖目录和历史验收脚本；这些本地文件保留不删除。
- 只使用普通提交和推送；不 force-push、不自动开 PR，不把推送失败写成成功。

## 源码测试的范围

- 常规测试只依赖仓库内的卡片、P2-C CLI 与合成数据；不再自动读取相邻旧工程或历史 report 目录。沙箱拒绝读取测试使用新建的外部临时文件，不读取用户的 shell 配置。
- Ring15 的整数枚举、精确分数、全路径/全前缀计数、随机差分、输入校验与阈值分支仍是默认测试。默认解析夹具是 15 个对称半单位数对，解析结果为 1/2；它不是历史 9/8 基线，也不是研究见证。
- 原 9/8 基线回放的断言保留为单独的可选测试，仅当用户显式设置 `DSH_MATH_RING15_BASELINE` 指向已核验的公开对象 JSON 时读取该文件。未设置时报告一个明确的 skip；本仓库不附历史材料，不宣称本次重新验证了该基线。
- 完整 Python 沙箱测试当前要求 macOS 的 `/usr/bin/sandbox-exec`、Python 3.10+ 与 Node；不能宣称 Linux/Windows 完整验收。JS 测试的假令牌和密码是负向/隔离夹具，不是真实凭据。
- 现有 [live_runner.mjs](<live_runner.mjs>) 仍含本地开发 profile 路径，不是通用安装或自动启动入口。本轮不修改这些原有包字节，也不据此启动模型或复制任何账号配置。

## 后续开发

仓库根目录即插件源码目录，默认 `origin/main`。测试、审查暂存差异、提交及推送约定见 [AGENTS.md](<AGENTS.md>)；新增根文件须先更新 [.gitignore](<.gitignore>) 的明确允许清单。

```bash
npm run build
npm test
git status --short
# 逐项检查并暂存本次任务的源文件，再审查 git diff --cached。
git commit -m "Describe the completed update"
git push origin main
```

命令示例不代替 UI/运行时变更所需的隔离安装、真实交互、哈希及独立导出验收。
