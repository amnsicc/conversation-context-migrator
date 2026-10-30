# 当前迁移器版本

- 当前迁移器规则版本：`3.3`
- 当前上下文单元协议版本：`3.0`
- 普通蒸馏：读取 `distill.md`、`schema.md`、`runtime-state.md`
- 全部历史或原文恢复：另读 `history-recovery.md`；先检查当前真实线程日志，缺口先说明，禁止摘要静默替代原文。
- 普通继承：读取 `inherit.md`、`runtime-state.md`
- Obsidian 同步：读取 `obsidian.md`；本机配置位于 `config/obsidian.local.json`
- 高级功能：仅按需读取 `optional-advanced.md`

当前磁盘规则优先于线程记忆和历史单元版本。版本无法确认时停止，不静默执行旧流程。
