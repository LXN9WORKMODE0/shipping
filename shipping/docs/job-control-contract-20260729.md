# M3.3 Job 控制与重启对账合同

日期：2026-07-29

## 目标

在项目内论文池已经支持部分成功的基础上，补齐长期批处理必须具备的控制能力：

1. 用户可以取消排队或运行中的 Job。
2. 服务重启后不把所有未完成 Job 混同为普通失败。
3. 已完成论文继续保留，未成功论文可以形成新的冻结 Job。

本轮不实现断点续跑。取消或中断后再次运行必须创建新 Job、新输入快照和新 run ID。

## 状态机

```text
queued
  -> running
  -> cancelled
  -> interrupted
  -> failed

running
  -> cancel_requested
  -> completed
  -> completed_with_failures
  -> cancelled
  -> interrupted
  -> failed

cancel_requested
  -> cancelled
  -> interrupted
```

终态：

- `completed`
- `completed_with_failures`
- `failed`
- `cancelled`
- `interrupted`

`cancel_requested` 是非终态。它表示取消意图已经写入账本，但执行线程尚未完成当前子进程收束。

## 取消行为

### 排队中的 Job

直接转为 `cancelled`，不启动子进程。

### 运行中的 Job

1. 先将账本写为 `cancel_requested`。
2. JobService 查找当前活动子进程并请求终止。
3. 已经写入 `paper_results` 的论文保持原状态和产物。
4. 当前论文不伪造为失败；未开始论文不生成结果。
5. 单篇分析 Job 在转为 `cancelled` 前，增量发布已经验证成功的论文 run。
6. Job 最终转为 `cancelled`。

取消不是失败，不增加失败论文数量，不自动重试，也不删除已完成产物。

## 重启对账

服务启动时扫描 `queued`、`running` 和 `cancel_requested`：

- `cancel_requested`：完成用户已经表达的取消意图，转为 `cancelled`。
- `queued`：转为 `interrupted`，不自动提交。
- `running`：保留已有 `paper_results`，单篇分析增量发布其中已验证成功的 run，然后转为 `interrupted`。

本轮对账以 Job 账本中已经原子记录的逐论文结果为证明边界。不根据“看起来存在”的临时目录猜测成功，也不自动认领在崩溃与记账之间产生但尚未记账的产物。

## 重新运行

仅 Card 和单篇分析 Job 支持逐论文重选。

重跑候选由冻结输入与 `paper_results` 做集合差得到：

- 明确 `failed` 的论文。
- `cancelled` 或 `interrupted` 时没有完成结果的论文。

已经 `completed` 的论文不进入候选。界面提供“选择上次未成功”，但仍要求用户通过现有预检对话框创建新 Job。

## 非目标

- 不恢复旧线程或旧 HTTP 请求。
- 不复用旧 Job ID、分析 run ID 或综合 run ID。
- 不在服务重启后自动继续调用外部 API。
- 不把取消中的论文记为失败。
- 不对完整流程做逐论文重跑；严格完整流程仍是整体冻结输入。

## 验收

1. 排队 Job 可取消且不会执行。
2. 运行中的多论文 Job 可取消，已完成论文保留，Job 为 `cancelled`。
3. 重启后 `running` Job 为 `interrupted`，`cancel_requested` Job 为 `cancelled`。
4. 单篇分析取消或中断后，已完成 run 可继续进入项目 Evidence 池。
5. API 返回稳定的重跑候选论文列表。
6. 前端可取消活动 Job，并一键选择最近 Job 的未成功论文。
