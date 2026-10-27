# 当前迁移器版本

- 当前迁移器规则版本：`3.2`
- 当前上下文单元协议版本：`3.0`
- 普通蒸馏：读取 `distill.md`、`schema.md`、`runtime-state.md`
- 普通继承：读取 `inherit.md`、`runtime-state.md`
- Obsidian 同步：读取 `obsidian.md`；本机配置位于 `config/obsidian.local.json`
- 高级功能：仅按需读取 `optional-advanced.md`

当前磁盘规则优先于线程记忆和历史单元版本。版本无法确认时停止，不静默执行旧流程。
