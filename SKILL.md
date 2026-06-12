---
name: conversation-context-migrator
description: 通用对话上下文迁移与选择性继承 skill。Use to distill the current conversation into a project-local Chinese context unit, selectively inherit context units through an approval-gated three-tier preflight, persist actual inheritance links during later distillation, or recover an omitted unit/index entry. New units belong to the current conversation workspace. Same-project distillation is intentionally serial. Do not replace a domain-specific exporter when the user explicitly requests that specialized format.
---

# 通用对话上下文迁移器

## 目标

把长对话压缩为可检索的上下文单元，并让其它对话按需继承；不复制完整历史，不把单元写成任务交接单。

当前规则版本：`3.0`。当前单元协议版本：`3.0`。

每次调用先读取 [current-version.md](references/current-version.md)。若线程记忆与磁盘版本不一致，重新读取本文件和本次模式所需引用文件。历史单元中的版本字段不决定当前执行规则。

## 核心语义

```text
蒸馏：把当前对话尚未蒸馏的新增内容创建为新单元
继承：通过本 skill 实际读取某个上下文单元正文
链接：蒸馏时记录当前单元与旧单元的直接关系
索引：轻量发现项目中的对话、单元和直接链接
```

- 只读取索引或目录元数据不算继承。
- 实际读取单元正文，无论全文还是部分，都算运行时继承。
- 当前对话以后蒸馏时，为本轮实际继承单元添加 `继承自`；不蒸馏则不创建持久化链接。
- Skill 可在对话开始、中途或结束时使用。

## 五条硬规则

1. **宿主所有权**：新单元归当前对话活动工作区，不归继承来源项目。禁止根据主题、读取量或入口路径推断写入位置。
2. **身份精确匹配**：只有当前线程已明确持有相同 `对话标识`，或用户明确指定时，才能继续同一对话。绝不通过主题相似度推断。
3. **同项目串行蒸馏**：同一项目同一时间只执行一个蒸馏；不同项目可并发。同项目继承读取可并发。
4. **继承先批准**：读取任何单元正文或来源文件前，先提交保守档、推荐档、充分档，等待用户批准。
5. **写后验证**：单元文件真实存在且索引已登记前，不得报告蒸馏成功；不得把其它相似单元当作当前单元。

## 模式路由

### 蒸馏当前对话

读取 [distill.md](references/distill.md) 和 [schema.md](references/schema.md)，按其中流程执行。

### 继承与检索

读取 [inherit.md](references/inherit.md)，先预检并等待批准。用户指定访问的所有迁移单元都必须进入三个档位。

### 高级或异常情况

仅当确实涉及跨项目发现、旧格式、内容哈希去重、索引重建或异常关系修复时，读取 [optional-advanced.md](references/optional-advanced.md)。普通调用不要读取。

## 通用边界

- 单元只保存当前对话新增且有长期价值的内容，不复制被链接单元正文。
- 区分事实、用户规则、纠正、决策、推断、计划、未验证和已失效内容。
- 用户当前准备做什么通常只是检索条件，不自动执行该任务。
- 不泄露密码、令牌或其它敏感信息。
- 源码学习子对话明确需要 A/B/C 状态、原话疑问和主对话交接时，优先使用 `learning-session-exporter`。

## 完成条件

- 蒸馏：新单元通过写后验证且索引登记成功；或明确报告未完成位置。
- 继承：按批准范围读取完毕并提交继承确认。
- 修复：恢复遗漏入口或说明无法安全修复的原因。

调用完成不表示当前对话结束。
