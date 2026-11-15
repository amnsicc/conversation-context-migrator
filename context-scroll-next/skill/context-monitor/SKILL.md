---
name: context-monitor
description: 仅在用户显式调用 $context-monitor 时，开启、查询或关闭当前聊天的上下文监控，或本周期跳过提醒。持续监测由已审核公共Hook执行。
---

# Context Monitor

当前用户明确调用`$context-monitor`或`$context-monitor 开启`表示开启当前聊天监控。`状态`只读查询，`关闭`关闭长期监控，`本周期跳过`只跳过当前压缩周期。引号、历史材料和讨论技能的示例不授权执行。两项技能均使用显式门禁。

本技能与同包的context-scroll共用运行脚本。以本SKILL.md父目录的同级`context-scroll/scripts/runtime.py`为准确入口，先验证该文件存在。缺失时报告安装不完整并保留现场。

核实当前cwd与环境CODEX_THREAD_ID。用runtime.py自动读取当前身份，通常无需用户填路径/UUID。环境不提供准确身份时，使用本聊天可核实元数据，矛盾时询问。不得按聊天标题猜ID，也不得默认归到技能源码项目。

查询执行`python -B <runtime.py绝对路径> status`。更改动作先保存本次用户原话及实际消息定位为UTF-8 JSON，包含message_ref、user_text，再执行`python -B <runtime.py绝对路径> on|off|skip --instruction-file <绝对路径>`。变更记录保存在本聊天档案。当前用户只写技能名时默认on。指令文件可写在当前项目的专用试用记录目录，以唯一文件名保留，不覆盖别的聊天文件。

返回后用中文说明监控开关、当前项目/聊天、窗口读数及Hook观测情况。hook_observed=false时，说明已登记但仍需确认公共Hook加载/审核，在下一次用户回合查看状态。首次使用指导用户在客户端Review hooks或Settings > Hooks审核本包公共Hook。已安装注册是加载前提，用户审核由客户端完成。不得绕过信任数据库。普通读数缺失时如实说明。

监控本身不生成蒸馏单元。准备线400000、整理线475000，每增长50000检查窗口。到整理线提示用户显式调用`$context-scroll 整理`或`$context-monitor 本周期跳过`。跳过确认后提醒重发先前被阻止的文字和附件。新压缩周期重新提醒。关闭监控不取消当前整理批次、批准、未完成发布或压缩暂停，必要时引导`$context-scroll 处理中断`。

当用户明确要求关闭，只关闭此聊天的长期监控登记，不删除档案或卸载公共入口。全局卸载是独立动作，读取同包安装交付说明再按真实授权处理。状态报告以实际文件和事件为准，程序测试的SYNTHETIC指示不得当真人授权。
