# 文件底座接口（开发阶段二）

## 第六阶段试用接口

安装后的context-scroll用当前cwd与CODEX_THREAD_ID确定身份，runtime.py的status/on/off/skip/arm/disarm/resolve分别管理查询、监控、临时保护和开始前未知压缩。变更必须提供含message_ref/user_text的实际instruction文件。on不begin，off不ack，不更改内容批准。resolve仅限没有活动批次的未知状态，有批次用原ack-interruption。

begin增加可选`--protect-compaction`。正常蒸馏技能要求该参数，底座兼容性/程序试验仍可直接begin。受保护begin核验同聊天、同项目、本版运行脚本哈希的实际公共Hook回执，拒绝未核定压缩。批次记录protect_compaction，让公共入口独立于长期监控继续处理压缩，真正关闭后结束。

coverage可带mode，continuous表示连续阶段，first_visible表示首次完整可见，bootstrap表示摘要/断档补录。bootstrap要求history_sources、known_gaps及真实scope_confirmation。正文职责和两官核验范围见bootstrap.md。覆盖元数据仍共同绑定候选集合，不能写完批准后再改。

仅定义本地存储操作，不是技能自动执行入口。源码版本与实际安装版本分别核对；显式调用由SKILL.md控制。程序不整理语义、不启动检查官、不判断用户是否真的批准。

## 调用与输入

`python <源码绝对路径>/store.py --project <已存在的绝对项目路径> --thread <准确小写UUID> <命令> ...`

每次输出一个JSON对象。退出码0成功、2输入无效、3状态冲突需处理、4文件/运行错误。`--help`是人工帮助例外。公共字段：`ok, operation, thread_id, batch_id, status, error`；命令另附具体结果。

| 命令 | 参数 | 行为 |
|---|---|---|
| status | 无 | 返回状态和未完成发布；新项目不创建档案；已有档案在锁内读取 |
| begin | --coverage-file；可选 --forked-from | 创建批次，返回batch_id和候选目录；不推进正式锚点 |
| save-material | --batch --source --purpose；可选 --large-approval-file | 复制到材料目录并绑定清单，原文件移动不影响副本；改变清单使检查/批准失效 |
| save-candidate | --batch --stage --handover | 保存UTF-8正文，返回candidates.content_hash；内容相同且完整则幂等 |
| prepare-publication | --batch；可选 --revision-mode / --instruction-file | 检查前准备正式位置的两稿，纳入新的content_hash，使旧检查/批准失效 |
| record-review | --batch --review-file | 记录一个角色的检查，角色为stage/handover；内容变动后旧报告不能复用 |
| record-approval | --batch --approval-file | 双检查通过后记录真实用户明确批准的依据 |
| publish | --batch | 验证内容、证据和批准后发布；重复同批次不重复推进 |
| recover | --batch；可选 --mode complete/rollback | 显式继续未完成发布或回退；外部改动/快照损坏则停止 |
| cancel | --batch --reason | 取消未发布批次，保留候选和材料；不改正式锚点 |
| ack-interruption | --batch --instruction-file | 用户明确指示后记录压缩中断处理依据，解除操作暂停；不自动恢复原文或发布 |

`save-material`和`save-candidate`还接受 `--revision-mode autonomous/user`，默认autonomous。已有检查后的改动计一轮自主修订，最多两轮；同轮中连续保存还未重新检查的稿件不重复计轮。两轮后应询问用户。用户要求的修改须提供 `--instruction-file`，包含 `message_ref,user_text`，不会伪装成第三轮自主修订。

## JSON文件格式

coverage-file：

```json
{
  "stage_key": "最近真实压缩或首次阶段起点的准确定位",
  "start": "上次压缩后首条消息的准确定位",
  "end": "本次整理截止消息的准确定位",
  "previous_compaction": null,
  "original_visibility": "原文完整可见，或明确说明哪些范围已被摘要替换"
}
```

尚未发生压缩时previous_compaction可为null，同阶段多批保持一致；发生压缩后填写实际依据。本地程序验证结构和已有绑定，不凭这些字符串证明原文完整或范围正确。

review-file：

```json
{
  "thread_id": "准确UUID",
  "batch_id": "begin返回的批次ID",
  "content_hash": "save-candidate返回的内容集合哈希",
  "role": "stage",
  "verdict": "pass",
  "reviewer_id": "实际检查官标识",
  "message_ref": "真实检查回执的消息或文件定位",
  "summary": "检查范围、发现和依据；不能只扫描正文中的通过二字"
}
```

verdict还可为 `revise/insufficient`。接手角色使用handover，检查官ID必须不同。两份报告均绑定整套候选，防止阶段稿改变后接手检查仍被当作有效。报告落盘后被改动也会拒绝发布。

approval-file：

```json
{
  "thread_id": "准确UUID",
  "batch_id": "当前批次ID",
  "content_hash": "当前内容集合哈希",
  "decision": "approve",
  "scope": "both",
  "message_ref": "用户明确放行消息的准确定位",
  "user_text": "用户原话"
}
```

填写这些字段是调用模型对真实来源的记录，不是程序认证。一个本地脚本无法证明调用者没有伪造用户或检查官；第四阶段由技能实际调用、回执与用户门禁保证流程。模型仍须正确解释用户意图，不能用自动填表代替询问和批准。

材料首版试用上限5MiB。超过时返回冲突，须先询问用户，再提供large-approval-file：`decision:copy_material, source:解析后的绝对来源路径, message_ref, user_text`。采用流式复制/哈希，不把整个大文件一次读入内存。不自动删除副本、候选或恢复备份；备份仅为恢复用途。

## 内容、身份与一致性

每聊天独立状态，resume重新指定同一ID即读原档案；fork使用新ID，可记录forked_from。UUID仅作准确身份，不能用标题替代。

集合哈希绑定聊天、批次、范围、开始时正式锚点、两份正文、材料清单；清单包含副本哈希。发布前重新核对实际内容；覆盖范围和来源完整性仍须模型核对。阶段序号只在正式发布成功后增加，取消批次不占序号。

主状态的 `phase_number` 表示最后已发布阶段序号。阶段7按下面的stage_key分配本批目标，同阶段可保持原序号。`base_anchor`固定为本批开始时的正式锚点，避免发布后锚点更新导致已发布候选被误报变化。

## 阶段7：同压缩阶段多次发布

技能创建的每批coverage必须带非空`stage_key`：使用最近真实压缩的准确定位；未发生过压缩则使用首次阶段起点的准确定位。它是来源定位，不生成随机压缩身份。相同阶段的key、start和previous_compaction保持一致，end记录本次实际截止，覆盖全阶段。新压缩才换key；不能用蒸馏次数、标题或占用下降代替压缩依据。

begin将目标写入并返回`stage_assignment`（number、stage_key、replaces_batch、observed_compaction），参与候选集合哈希。新key增加阶段，相同key更新最新阶段；不能重开旧阶段、改变同阶段起点，或绕过已观测的新压缩/未知状态。两批之间监控和保护可能关闭，旧Hook观测不变不能证明没有新压缩，新阶段仍须填写不同的真实压缩来源，由模型和检查官核实。同阶段批准前正式两稿不变，批准后仍用原六目标事务发布已审定字节。阶段目录只列每个阶段一次，publications按批保留历史；`.恢复备份/<batch>/0`保存被替换阶段字节，接手旧字节在同批备份1。它们保留实际覆盖，不补造压缩前未整理的尾段。

旧档案没有stage_key时，先核对当前真实压缩边界与最近正式阶段的coverage。若仍是同阶段，在coverage中加`replace_batch`指定最近正式批次ID，沿用原start/previous_compaction，以当前准确定位建立key；若确有新压缩，则用新key新建阶段。身份不足先问，不扫描猜测或批量迁移旧档案。底座兼容旧无key调用的递增行为，仅用于旧接口/程序回归；技能不能采用该路径。已有key的档案不允许退回无key调用。

已有候选或未完journal继续按旧绑定处理，不强行改目标、编号或旧批准。终态凭据仍绑定原事务及publication；同阶段批准更新后，正式字节校验以该阶段最新publication为准，旧快照/备份保留。

路径写入限制在宿主的 `上下文维护` 目录内，拒绝链接/Windows重解析点和越界。模型选定的外部材料源允许读取，副本目标由程序生成。输入范围和宿主须由模型根据当前聊天准确传入，脚本不能识别所有会话真实归属。

所有操作按“项目锁→聊天锁”取得OS锁；项目锁保护共享项目目录，代价是同项目不同聊天的写入短暂串行。等待锁最多3秒，超时明确返回可重试冲突，不限制整个开发或整理耗时。进程退出由OS释放锁；保留lock文件不代表仍被锁住。

发布前准备快照与恢复副本，再写journal；依次发布阶段、接手、聊天目录、项目目录，最后提交状态/锚点，再将journal标记完成。状态与journal共同判断是否发布完毕。直接打开文件可能短暂看到中间状态，不宣称多文件瞬间原子切换。

有未完成journal时，本项目新的存储修改暂时拒绝，先明确指定该聊天recover，防止其他聊天更新共享目录干扰恢复。读status会列出待恢复聊天。恢复只接受磁盘内容等于操作前或操作后哈希；发现额外编辑则保留现场，不覆盖用户修改。

故障保证针对已测试的进程退出和文件操作失败；不承诺断电、磁盘损坏或任意恶意篡改下仍可无条件恢复。修改过的正式接手不能直接被下一批覆盖，须先解决差异；当前没有自动合并用户外部修改的入口。

## 存储职责

本脚本实现文件和记录的可靠流转；模型写作和真实双检查由技能及协作工具执行，不在Python中伪造。用量/事件由单独hooks.py处理；本脚本检查压缩中断sidecar，在明确用户指示前拒绝继续修改，不新增压缩前尾步核对。Hook接线与实测范围见hooks.md，不由本地状态文件存在推断所有宿主/UI行为均已完成。

## 第二阶段复验修订（2026-11-14）

聊天目录哈希现在也登记到该批正式publication.formal_hashes。共享项目目录的基线单独保存在项目级 `.状态/project-baseline.json`，只在项目锁内访问；后续聊天使用项目最新基线，不使用自己的历史项目目录哈希。发布前发现目录被外部修改或缺少已提交基线时停止，保留目录和正式锚点，不自动合并或认领当前文件为基线。

发布事务新增项目基线目标，因此journal的operations为六个，顺序是阶段、接手、聊天目录、项目目录、项目基线、聊天状态。项目基线和其他目标一起准备快照/备份，支持继续及回退。多文件仍非瞬间原子；过程中基线文件可能已写入，但journal未结束时不能视作新已提交基线，本项目新修改会被阻断，必须先恢复。

新journal为schema_version=0.2，状态仅允许pending/committed/rolled_back，结构、线程/批次/事务ID、目标/快照/备份布局和哈希形状均检查。异常日志也列入pending_publications；status另附transaction_issues原因，本聊天异常显示journal_conflict。不能只看列表名称将异常当成普通可自动恢复pending。

全部目标完成后，先写本事务独立完成凭据 `.状态/receipts/<transaction_id>.json`，再结束journal。凭据绑定不含可变status/finished_at的journal内容哈希、完成方向和对应发布记录。终态recover先核对凭据与当前发布记录；committed还核对不可变阶段文件，不能凭状态单词返回already_recovered。凭据写完但journal未结束的中断，只允许按已完成方向补完。

历史完成凭据不要求当前接手、目录、state一直等于旧快照：它们可由新批次及其他聊天合法更新。回退完成后仍可取消旧批次并开始新批次；没有取消的回退批次仍待处理，不自动假设用户放弃。凭据不是防伪签名，不宣称抵御任意恶意同步重写全部文件。

旧0.1日志不自动迁移为可信终态；版本/凭据不足明确停止并保留现场。升级脚本不能直接在旧试验档案上发布来“修好”记录。当前仍是候选源码，无生产档案迁移，本轮不改独立验收隔离现场。

## 第四阶段：先固定待发布字节

save-candidate之后、检查之前调用prepare-publication。它在本批`.待发布/stage.md`与handover.md保存正式位置下的版本；state.publication_plan记录候选源哈希、两个输出路径/目标/哈希。snapshot把plan纳入content_hash。两位检查和用户批准均针对这个完整集合。候选正文、材料或待发布文件改变，旧检查/批准不能直接沿用。

prepare-publication相同内容重跑幂等；若在已有检查后准备出不同内容，计入自主修订。save-candidate或添加材料使plan失效，再准备不重复计同一轮。主状态已发布序号变化时，snapshot按本批publication记载的实际阶段号复核，不误判旧plan。

支持单行Markdown内联链接/图片（含平衡嵌套方括号标签）和单行引用式定义；显式引用块中的普通链接/定义也先分离并保留引用前缀，再换算URL。路径含空格或括号需URL编码。相对路径按候选实际位置解析，再换算到正式目标；配套两稿映射到正式阶段/接手，材料和历史候选清单仍指向保留的来源。HTTP等远端链接保持原样，不联网验证。

这里的嵌套标签仅指纯文字方括号，不包含标签内部的活动图片或链接。图片作为链接文字、内层内联链接、内层引用式链接/图片均在prepare明确拒绝，提示拆成独立简单链接，不只换算外层。对可能激活内层shortcut引用的定义也保守拒绝；定义形状即使出现在字面区也可能导致保守拒绝，不宣称完整CommonMark上下文解析。受支持的顶层独立图片及普通纯文字嵌套标签不受此限制。

只保护可明确识别的顶层围栏代码和单行匹配的行内反引号跨度，字面内容和换行保持原样。四空格、制表符或混合缩进的非空行（含引用块中的对应形式）明确拒绝，要求在检查前转换为顶层围栏代码；不尝试猜测是缩进代码还是列表容器内容。引用块中的围栏、列表容器里的引用定义、跨行/不平衡标签、嵌套非内联方括号结构、不匹配的行内反引号、HTML href/src及其他不支持的目标语法也会拒绝。准备器采用有限扫描规则，不是通用Markdown解析器；不把“受支持代码保持字面”概括成所有代码块都已支持。拒绝发生在写待发布稿/登记plan之前，保留已保存候选和状态，不能把拒绝当作内容检查通过。

publish有plan时只复制plan中已经绑定哈希的两个输出字节。没有plan的旧纯文本底座测试仍可发布；包含链接的稿件则拒绝直接publish，不能靠省略准备绕过链接适配后的检查。正常技能流程不论是否有链接都prepare。正式输出与审定字节完全相同，发布时不会替换正文中的“未发布”字句，模型应在检查前采用以状态记录为准的生成说明。

报告JSON可附findings、read_scope、limitations等字段，store会原样保存并绑定报告哈希，但不验证它们是否真实。实际来源与检查官身份由当前模型核对工具回执。收到任一新报告会清除已有批准，防止晚到的报告与用户已批准结果不一致。两个pass只是awaiting_approval，没有record-approval就不能发布。

## 第五阶段：压缩中断操作门禁

hooks.py在整理途中压缩后保存`.状态/interruption.json`，保留batch/候选位置/当时进度。status返回interruption；存在awaiting_user时拒绝修改（包括publish/recover），不能靠新请求绕过。当前state/journal不被Hook重写为伪完成。

活动判断也包含本聊天准确批次未完成/冲突的发布事务，不能仅看state.status。只有本批仍需处理的事务才允许对published状态记录中断并ack；真正已完成批次仍关闭，其他聊天的事务不归到本批。status中awaiting_user优先显示interrupted，事务原因继续保留在transaction_issues。

第五阶段修订001增加不可变compact-inbox通知及compact-receipts处理凭据。压缩Hook在取主锁前只追加通知，主状态仍在锁内更新；锁超时后status报告compaction_pending。status只读不消耗通知；后续修改/ack先在锁内落实压缩元数据，有活动中断时仍等待明确指示，不能因释放锁就接受save/publish/recover。ack可直接处理通知，无需重投Hook；处理通知不等于继续候选写作、业务或journal恢复。通知损坏/身份不明明确阻断并保留现场。详细边界见hooks.md。

修订002：处理凭据只证明通知处理完成，不单凭内容hash认领后续缺身份事件为旧压缩。SessionStart追加带delivery_id的新通知并在锁内重新关联；未核定的Post凭据同样不作永久重复证明。关联不足就cycle_uncertain=true、skip_cycle=null，并以pause_notification区分新一次未知暂停与旧ack；真实compact_key仍标缺失，不用随机投递编号伪造压缩身份。可验证的旧事件不覆盖当前未知暂停或已记录的处理指示，新可靠事件和用户明确ack有正常处理路径。该修订不改变journal恢复、发布、批准或项目锁规则。

用户明确指示后，主模型用UTF-8 JSON记录 `decision:resume_after_compaction, message_ref, user_text`，调用ack-interruption。它只记录用户已指示处理，既不认证真人身份，也不等于内容批准或自动补原文；原批次还在，取消或恢复仍是后续明确动作。如果提醒状态因压缩身份缺失而被暂停，明确指示后会清旧跳过/用量并解除该操作暂停；不猜回丢失的真实压缩ID。
