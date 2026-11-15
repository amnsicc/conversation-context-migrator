# Codex 上下文维护（阶段 7）

本目录包含两个独立技能。 `context-monitor` 管理当前聊天的用量提醒和本周期跳过。 `context-scroll` 在显式调用后提炼当前阶段，并更新累计接手单元。根目录的 Conversation Context Migrator 继续提供指定来源蒸馏、选择性继承和 Obsidian 图谱。两套技能各自维护自己的归档。

同一个压缩阶段可以多次蒸馏。每次从阶段起点整理到本次截止，由两位检查官分别检查阶段单元和接手单元。用户批准后才更新正式文件，旧稿保留备份。是否压缩以及何时压缩由用户决定。

## 安装和使用

需要 Python 3.11 或以上版本。下载 v3.5.0 的 `context-scroll-trial-0.7.zip`，解压后在解压目录运行安装器。以下为 Windows PowerShell 示例，备份目标使用一个新的目录。

```powershell
python -B installer/install.py install --home "$env:USERPROFILE/.codex" --output "./installation-backup"
```

安装器会注册七个公共 Hook。按 Codex 界面的提示审核 Hook，再在聊天中使用技能。已有同名技能时，安装器会停止并保留现有文件。原安装可凭该次安装生成的 `installation.json` 卸载，见下例。原迁移技能可以继续使用。

```powershell
python -B installer/install.py uninstall --receipt "./installation-backup/installation.json"
```

- `$context-monitor` 开启当前聊天的监控。
- `$context-monitor 状态` 查询监控状态。
- `$context-monitor 本周期跳过` 跳过当前压缩周期的整理提醒。
- `$context-scroll 整理` 开始一批蒸馏。
- `$context-scroll 查看进度` 查看当前整理状态。

阶段 7 已完成项目验收。测试范围包括隔离环境中的发布和恢复、核心 Hook 行为，以及短文本的语义检查。真实桌面长对话和跨版本使用仍待实测。
