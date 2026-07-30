# 14篇论文 Review Framework 验收记录

## 验收对象

- 来源Landscape：`landscape-14papers-20260730-v9`
- 来源题录：`references-14papers-v4-20260729`
- 接受运行：`framework-14papers-20260730-v2`
- 正式输出：`workspace/_review_frameworks/runs/framework-14papers-20260730-v2/output/review_framework.json`
- 人工报告：`workspace/_review_frameworks/runs/framework-14papers-20260730-v2/review/review_framework.md`

## 输入重放

- Landscape维度：7
- 论文：14
- 完整题录：14
- 部分题录：0
- Framework输入哈希：`sha256:90774c3b66bc6d2b0ac827142e2a16a13f8b6a8ff43c01adbfa5b4621bc81fc2`

程序从显式运行ID读取来源，重放Landscape原始响应及14篇Understanding，不扫描目录选择最新结果。Landscape目录名、manifest运行ID、题录目录名、题录manifest和catalog运行ID必须一致。

## 接受运行结果

- 状态：`completed`
- 章节：9
- 正文章节：7
- 正文维度覆盖：7/7
- 正文论文覆盖：14/14
- 未进入正文论文：0
- 题录问题：0
- 输入Token：5,510
- 输出Token：2,859
- `finish_reason`：`stop`
- 隐藏推理字符：0

## 模型与程序边界

模型只生成工作标题、中心问题、章节问题、章节用途、dimension组合、比较任务、争议、语料缺口和语料限制。以下字段由程序确定性派生：

- `framework_id`
- `section_id`
- `paper_ids`
- `contribution_ids`
- `citation_keys`
- `bibliography_status`
- `unused_papers`
- `bibliography_issues`

因此题录字段补全不会改变Framework与Section语义ID，模型也不能在章节规划时伪造来源ID。

## 失败与修正

`framework-14papers-20260730` 的结构合同通过，但人工审视发现两项比较任务点名了不在本节 `paper_ids` 中的论文。后续知识包严格按本节论文装配全文，这会让写作模型被要求比较不可见资料。

程序新增确定性规则：`required_comparisons` 点名某篇语料内论文时，该论文必须来自本节所选dimension或controversy；需要跨维度比较时必须显式绑定相关dimension，不允许后续阶段偷偷补论文。v2通过该规则。

## 结构审视

v2没有机械复制“一维度一章”：工程措施与运输组织、调度与智能平台进行了有依据的跨维度组合。引言和结论读取全体维度，七个正文章节完成所有维度和论文覆盖。比较任务属于待写作阶段回答的问题，不视为已经成立的文献结论。

## 结论

Review Framework Task 7通过，可以作为章节知识包的唯一章节来源。后续不得让写作阶段自行增加本节未绑定论文。
