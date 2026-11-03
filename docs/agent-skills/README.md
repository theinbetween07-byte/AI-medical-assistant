# Agent 技能基线（MedScribe HK）

> 作用：把安装文档里的技能清单固化为团队仓库的一部分，保证换机器、换 agent、换成员时恢复同一套能力，并规定“什么时候用哪个技能”，避免同类技能互相抢触发。
>
> 事实来源：仓库根 `.opencode/skills/` 实际目录、`skills.lock.json`（提交号锁定）、仓库根 `outputs/medscribe-测试验收与质量优化方案.md` §4.5（技能规范映射）。
> 安装位置：仓库根目录 `.opencode/skills/<name>/`，目录名必须等于 `SKILL.md` 的 `frontmatter name`。

作用域说明：本仓库根是 `team-project`。如果只把 `medscribe-allinone` 子目录作为工作区打开，技能不会自动加载；请从 `team-project` 根打开。不要在两个位置各放一份，避免版本漂移。

## 1. 已安装清单（2026-11-01 实测）

| 技能 | 层 | 来源（锁定提交） | 文件数 | 在本项目中的用途 |
|---|---|---|---:|---|
| `conventional-git` | 交付 | `samber/cc-skills@123cb155` | 2 | 分支、worktree、提交信息、changelog |
| `code-review` | 交付 | `mattpocock/skills@59db609e` | 2 | Standards / Spec 双轴评审 |
| `handoff` | 交付 | `mattpocock/skills@59db609e` | 2 | 轮班交接、超长会话交接 |
| `make-repo-contribution` | 交付 | `github/awesome-copilot@b6483db4` | 3 | 仅用于外部仓库贡献 |
| `hmi-design-full` | 设计规范 | `timhok/hmi-design@28829666` | 15 | 人因、层级、报警语义、色彩约束 |
| `design-taste-frontend` | 设计落地 | `Leonxlnx/taste-skill@12af471f` | 39 | 浏览器实测、版式、反模板化 |
| `industrial-brutalist-ui` | 设计风格 | `Leonxlnx/taste-skill-classics@5e08c938` | 2 | 仅宣传页/演示大屏 |
| `app-ui-ux-best-practices` | 设计系统 | `touchine-ojo/OJO-Design-Skills@fbd2c2d1` | 10 | token、组件规范、设计审计 |

另有仓库原有的 `skill-creator`（用于创建/更新技能），不属于第三方基线，不锁提交号。

安装清单中的第 5 项 `git-workflow-and-versioning` 是 `conventional-git` 的替代方案，按安装文档默认不装；若团队决定改用它，替换锁文件中的对应条目，不要两者并存。

校验结果：

- 8 个目录名与 `frontmatter name` 全部一致。
- `hmi-design-full/references/` 为 7 个文件；`design-taste-frontend` 共 39 个文件；OJO 技能共 10 个文件。
- 每个 `SKILL.md` 的 `metadata.source` 已写入“仓库@提交号”，便于追溯。

## 2. 一键恢复

```powershell
# 在仓库根目录执行；默认只安装缺失项
powershell -ExecutionPolicy Bypass -File .\docs\agent-skills\install-skills.ps1

# 先看计划，不落盘
powershell -ExecutionPolicy Bypass -File .\docs\agent-skills\install-skills.ps1 -DryRun

# 只装某一个
powershell -ExecutionPolicy Bypass -File .\docs\agent-skills\install-skills.ps1 -Skill hmi-design-full

# 覆盖已存在目录（先备份为 <name>.bak.<时间戳>）
powershell -ExecutionPolicy Bypass -File .\docs\agent-skills\install-skills.ps1 -Force
```

安装后校验：

```powershell
Get-ChildItem .opencode\skills -Directory | ForEach-Object {
  $p = Join-Path $_.FullName 'SKILL.md'
  if (-not (Test-Path $p)) { "MISSING  $($_.Name)"; return }
  $n = (Select-String -LiteralPath $p -Pattern '^name:\s*(.+)$' -Encoding UTF8 |
        Select-Object -First 1).Matches[0].Groups[1].Value.Trim()
  "{0,-8} {1,-28} -> {2}" -f $(if ($n -eq $_.Name) { 'OK' } else { 'MISMATCH' }), $_.Name, $n
}
```

DeepWorks 只从项目 `.opencode/skills/` 加载项目技能；刷新技能列表后应能看到上述技能。`handoff` 与 `industrial-brutalist-ui` 设置了 `disable-model-invocation`，不会自动触发，必须按名调用。

## 3. 什么时候用哪个

| 阶段 | 首选技能 | 不要同时点名 | 产出 |
|---|---|---|---|
| 需求/方案 | 无专用技能；用 PRD、技术设计作 Spec | — | 方案、范围、验收口径 |
| 分支/提交 | `conventional-git` | `chinese-commit-conventions` | 分支名、commit、changelog |
| 实现前拆分 | 现有 `writing-plans` / `subagent-driven-development` | — | 可执行计划 |
| 评审 | `code-review` | `requesting-code-review` / `receiving-code-review` | 双轴结论 |
| 交接 | `handoff`（显式调用） | — | 临时目录中的交接文档 |
| 外部仓库贡献 | `make-repo-contribution` | 本地提交技能 | 合规的 issue/PR |
| 设计规范 | `hmi-design-full`（安全/可读性底线） | — | 评审清单、色彩/层级规则 |
| 设计系统 | `app-ui-ux-best-practices` | — | token、组件规范、审计 |
| 页面落地 | `design-taste-frontend` | — | 浏览器实测与截图修正 |
| 宣传/演示大屏 | `industrial-brutalist-ui`（显式调用） | — | 风格化页面，不进入临床主流程 |

## 4. 冲突与优先级

1. **提交格式二选一**：本项目选 `conventional-git`。提交主题用英文祈使句（≤72 字符），正文可用中文；不再同时启用 `chinese-commit-conventions`，避免同一句话被要求两种格式。
2. **评审只用一个入口**：说“双轴评审”时走 `code-review`；不要把 `requesting-code-review` 和 `code-review` 放在同一句里。
3. **设计规范优先于观感**：`hmi-design-full` 与 `industrial-brutalist-ui` 冲突时，以 `hmi-design-full` 为准。临床记录、校审、签署、导出界面禁止使用 CRT 暗色、全大写数据、航空红装饰。
4. **`design-taste-frontend` 不用于核心工作流重做**：它适合展示页或单屏重设计，单次成本约 30–45 分钟；核心工作流用 `app-ui-ux-best-practices` + `hmi-design-full`。
5. **`make-repo-contribution` 只用于别人的仓库**：在本仓库内它会把构建/测试交还给人工执行，反而降低效率；本仓库用 `conventional-git` + `code-review`。
6. **同名技能作用域**：全局 `app-ui-ux-best-practices` 与本项目副本同名，项目副本优先。只修改项目副本，避免两台机器行为不一致。

## 5. 分支、提交与 PR 规约

- 分支：`<type>/[issue-]<description>`，全小写、连字符、描述 ≤50 字符，例如 `test/12-acceptance-matrix`。
- 提交：Conventional Commits v1.0.0；`type` 取 `feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert`；主题英文祈使句、首字母小写、结尾不加句号。
- 破坏性变更：标题加 `!` 或页脚写 `BREAKING CHANGE:`。
- 关闭 issue：`Closes #12` 放页脚，不放标题。
- 禁止在 commit 中加入 AI agent 署名或 `Co-authored-by` trailer。
- squash merge 前先改对 PR 标题，否则 changelog 会静默失效。
- PR 描述用中文，至少包含：变更点、验证命令与结果、风险、回滚方式、关联 AC/FR。

## 6. 升级与漂移处理

1. 升级时只改 `skills.lock.json` 中的 `commit`，不改目录名。
2. 全队重跑安装脚本；`-Force` 覆盖前会备份旧目录。
3. 升级后必须重跑第 2 节校验，并抽查 `hmi-design-full/references/` 数量。
4. 上游把技能改名或迁移仓库时，先在 `skills.lock.json` 记录 `sourcePath` 变化，再更新脚本；不要凭记忆猜路径。
5. 本文件只含公开仓库与公开提交号，可安全提交；不要把 token、私有仓库地址、本机路径写进来。

## 7. 已知限制

- `handoff` 的文档写在操作系统临时目录，不写入仓库；这是技能设计，不是缺陷。
- `code-review` 的 Spec 轴需要 issue tracker 或明确的 Spec 文件。本项目用 `mvp ai medical assistant-prd.md` 与 `technical-design.md` 充当 Spec；评审时在提示中给出路径。
- OJO 技能要求先确认风格方向再产出 token；与 `hmi-design-full` 联用时，先定安全约束，再定风格。
- 技能只是流程约束，不替代测试证据。任何“完成”结论仍以命令输出、截图、验收记录为准。
