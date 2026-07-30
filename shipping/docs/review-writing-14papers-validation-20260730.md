# 14篇论文章节写作验证记录

## 结论

第9项“章节写作与全文装配”已经完成实现并通过真实语料验证。
正式接受的运行是 `writing-14papers-20260730-v2`。

该运行按Framework的9个章节执行9次隔离的DeepSeek调用。每次调用只读取当前章节的不可变知识包；9章全部通过Schema、引用Key和机器ID检查后，程序才确定性生成全文、资料范围和参考文献。

当前输出是“待Claim审计的正式草稿”，不是可直接发表稿。全文编辑状态为 `not_run_audit_required`。

## 运行结果

- Framework：`framework-14papers-20260730-v2`
- 写作运行：`writing-14papers-20260730-v2`
- 论文：14篇
- 章节：9章
- 段落：57段
- 段落引用：148次
- 被引用论文：14篇
- 正文字符数：25,098
- 输入Token：1,934,876
- 输出Token：16,109
- 总Token：1,950,985
- 失败章节：0
- 正文机器ID：0

正式草稿位于：

`workspace/_review_writings/runs/writing-14papers-20260730-v2/review/review_draft.md`

运行报告位于：

`workspace/_review_writings/runs/writing-14papers-20260730-v2/review/review_writing_report.md`

## 暴露并修正的问题

第一次真实运行 `writing-14papers-20260730-v1` 的9次provider请求均正常结束，但第3章有一段 `citation_keys` 为空，因此正式综述未发布。程序保留了8章有效中间结果、失败章节原始响应和失败账本。

进一步审查发现，模型曾把 `ref_...` 写入正文text，再由程序追加引用，造成机器Key污染和重复引用。原合同只约束引用数组，没有检查正文字符串。

处理方式不是清洗或兜底，而是：

1. 在Prompt中明确引用Key只能进入 `citation_keys` 数组。
2. 在验证器中逐段拒绝citation key、Evidence、Card、Material、Contribution、Package、Section等机器ID。
3. 将系统Prompt哈希纳入知识包运行重放条件。
4. 因Prompt变化，旧知识包被明确拒绝，并重新生成9个 `writing-v2` 知识包。
5. 重新执行全部9章，全部通过后才发布正式草稿。

## 当前判断

章节隔离、失败保留、正式发布门和确定性装配已经闭合。草稿在结构、跨论文比较和资料边界表达上明显优于旧Evidence-only草稿。

尚未验证的是逐项事实支持情况。尤其需要检查：

- 数字和效果幅度是否由对应论文直接支持；
- 仿真、算法benchmark、系统设计和工程应用是否被正确区分；
- 模型综合判断是否被误写成论文共识；
- 引用论文是否真正支持同段中的全部核心事实。

这些问题进入第10项正文Claim审计，不在写作阶段用改写或删除方式掩盖。
