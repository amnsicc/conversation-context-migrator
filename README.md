# Conversation Context Migrator

面向 Codex 的对话上下文迁移 skill：增量蒸馏、选择性继承，以及 Obsidian 单元关系图。当前规则版本为 **3.2**，单元协议版本为 **3.0**，支持兼容读取旧单元。

迁入背景用于补充或更新当前对话，保留当前对话原有上下文；有实质冲突且用户未裁定时，先询问用户。

## 安装与调用

将本仓库克隆至你的 Codex skills 目录中的 `conversation-context-migrator` 文件夹，然后在对话中明确调用：

```text
$conversation-context-migrator /蒸馏
$conversation-context-migrator /推荐继承
$conversation-context-migrator /充分继承
$conversation-context-migrator /检查索引
$conversation-context-migrator /同步图谱
```

继承正文前会提交三档读取方案，批准后才读取。实际读取范围、蒸馏锚点和直接来源分别记录。持久化读取记录不代表正文仍在当前模型上下文中。

详细规则见 [SKILL.md](SKILL.md)。

## Obsidian 可视化

每个节点是一个真实蒸馏单元。仅“继承自”和“继续自”生成 **来源 → 后续** 的箭头；其它关系显示在单元详情中。话题通过标签筛选、颜色分组，不因话题相同而生成继承箭头。

需要 Python 3.10+，脚本只使用标准库。将 [示例配置](config/obsidian.example.json) 复制为 `config/obsidian.local.json`，填写已授权的扫描范围和现有库路径。本机配置被 Git 忽略，不随仓库发布。

```text
python scripts/context_graph.py sync --config config/obsidian.local.json --dry-run
python scripts/context_graph.py sync --config config/obsidian.local.json
```

历史单元只生成展示副本；原文件、索引和身份保持不变。重复同步不会重复生成节点或改写无变化的笔记。已配置后，执行 skill 的继承登记或蒸馏流程会自动同步；这依赖智能体遵循 skill 流程，并非宿主应用事件钩子。

初次设置图谱前，正常退出正在使用该库的 Obsidian，然后运行：

```text
python scripts/context_graph.py sync --config config/obsidian.local.json --configure-graph
```

报告 `graph_configuration.status` 为 `applied` 后重新打开应用。应用运行时返回 `pending_app_close`，待退出后重跑，避免配置被应用缓存覆盖。普通同步不会更改图谱界面设置。

全范围筛选示例：

```text
path:"上下文迁移/" tag:#ccm/unit
```

单项目筛选示例（实际项目目录由同步报告给出）：

```text
path:"上下文迁移/<项目目录>/单元/"
```

更多行为、恢复方式和边界见 [Obsidian 说明](references/obsidian.md)。

## 验证

```text
python -m unittest discover -s tests -v
```

测试使用临时目录中的合成单元，不需要个人笔记库。公开仓库不包含本机配置、真实对话单元、笔记副本、截图或私人验收记录。

## 开发历史

早期开发没有 Git 提交记录。本仓库从保留下来的版本备份重建了部分历史，重建提交明确注明来源与日期依据；其作者日期对应备份中可确认的版本时间，提交者日期是实际导入时间。没有版本快照的日期未补造提交，文件时间也不能证明完整开发过程。详情见 [HISTORY.md](HISTORY.md)。
