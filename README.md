# Skill Auditor

给已有 Agent Skill 做通用质量体检，结合用途输出问题、证据和最小修复。适用于单文件与目录形式，覆盖创作、知识指导、工具调用等任务的共性；领域正确性仍需专业依据或实际验证。

## 直接使用

把 Skill 交给已加载本技能的 Agent，说“帮我审一下这个 Skill”即可。通常不必提供评分规则、目录要求或填写表格；审查者会先从材料理解用途，只有关键判断缺依据时才询问。

默认收到的是短报告：能不能用于指定任务、最值得改的问题、怎么改，以及还没验证什么。无需阅读下面的开发说明，也无需为了完成审查先安装 Python。

## 适应变化

检查任务是否明确、执行是否有依据、结果是否可验收等长期问题；平台字段、调用方式、动态资源与模型能力在需要时核对实际环境。遇到新格式先识别，遇到未知能力先核实，无法确认时保留受影响项。工具名称或平台变化不会自动变成目标缺陷。

适配方法与资料入口见 `references/platform-adaptation.md`。这能减少对旧规则的依赖，但不能承诺未来固定年限内免维护或对所有领域准确。

## 审查方式

先了解用途、使用者、环境与输出，再检查选择边界、执行指导、输出验收、一致性资源、依赖异常。按需启用写作、工具、副作用、共享维护等场景项。

默认不打总分，不要求七层目录，不按文件数量判成熟度。每项使用“通过 / 有问题 / 不适用 / 待确认”；最终结论为“需先修复 / 信息不足 / 暂可用”。内部按 P1/P2/P3 排序，默认向用户说明必须改、建议改或可选改进，并附位置、影响和修复。

规则由本项目维护，见 `references/skill-standard.md`，不代表行业标准或平台认证。旧版七层视角仅保留作问题定位。

## 使用

将整个目录放入目标 Agent 支持的 Skill 目录，让 Agent 加载后提出：

> 用 skill-auditor 审查 <目标 Skill 路径>。它用于 <用途>，给 <使用者> 在 <环境> 使用。

上下文可以从目标材料推断，不必每次填写。仅有 SKILL.md 也能审查，报告会说明资源与环境的可见范围。默认静态读取，不执行被审 Skill 的脚本或外部操作。

## 本地脚本与测试

以下是可选的自动盘点与开发测试说明；无运行环境时使用可用工具人工盘点即可。

运行环境：Python 3.10+、PyYAML 6.x；无需 API、登录态或凭证。建议使用独立虚拟环境：

```bash
python -m venv .venv
# Windows: .venv/Scripts/python.exe；macOS/Linux: .venv/bin/python
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe scripts/check_structure.py /path/to/skill --pretty
.venv/Scripts/python.exe scripts/check_structure.py /path/to/SKILL.md --pretty
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

若 Windows 安装 PyYAML 时提示缺少 C++ 编译器，可先在 PowerShell 设置 `$env:PYYAML_FORCE_LIBYAML="0"`，再重试安装，以使用纯 Python 实现。

脚本只读本地文件，JSON 打到 stdout；退出码 0 表示完成盘点，2 表示路径、读取或依赖错误。0 不代表目标质量合格。缺失/无效 frontmatter、必要资源等作为快照事实返回，交给审查者判断影响。

v0.3 的 JSON 使用 `schema_version: 2`：保留 `skill_md`、`directories`、`ghost_references`、`warnings`；新增 `scanned_documents`、`external_references`、`unlinked_files`。移除 `tier_hint`、`critical_checks`、`missing_critical`；原 `orphan_files` 改为 `unlinked_files`，不再直接断言文件无用。旧消费者需更新字段读取。

目录输入递归读取入口引用到的本地 Markdown 文件，记录二级引用；单文件输入只读该文件并核对其路径，不递归或盘点同级目录。支持常见反引号路径、Markdown 内联链接和引用式链接、章节锚点分离、路径中的空格与反斜杠。绝对路径、URL 和越出目标目录的引用仅列出待核实，不联网、不读取根目录外文件。

限制：正文中没有反引号或链接标记的裸路径、动态路径、复杂嵌套 Markdown、隐式工具加载和完整命令行中的资源参数需要人工核实；常见解释器命令不会当作文件路径。不检查锚点本身。缺失引用可能是示例，未发现引用的资源可能由代码使用，均需人工确认。完整性不能由空清单推断。

v0.4 在 v2 JSON 中补充 `skill_md.frontmatter_keys`（入口元数据字段名）、`deferred_references`（运行时路径或模式，未展开）和 `ambiguous_references`（多种基准都有文件，未递归）。默认 `--path-base auto` 同时考虑文档目录和 Skill 根目录；已核实宿主基准时指定 `--path-base skill` 或 `--path-base document`。候选文件存在不能证明宿主会选择它；没有元数据字段只报告事实，不决定该平台是否必需。

## 文件与维护

- `SKILL.md`：入口、工作流与关键约束。
- `references/skill-standard.md`：本项目当前判定规则。
- `references/checklist-7-layers.md`：通用和场景检查、可选七层定位。
- `references/boundary-rules.md`：外部工具与执行职责。
- `references/platform-adaptation.md`：平台差异、时效与能力核实。
- `references/anti-patterns.md`：问题分类索引。
- `assets/review-report-template.md`：审查报告模板。
- `scripts/check_structure.py`：文件事实盘点。
- `tests/test_check_structure.py`：脚本回归测试。
- `tests/behavior-cases.md`：不同用途 Skill 的人工验收案例。

更改规则时同步模板与案例；工具/平台约束变化或发现真实误判时更新对应条目。变更记录见 `CHANGELOG.md`。
