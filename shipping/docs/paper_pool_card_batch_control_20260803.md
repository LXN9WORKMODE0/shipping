# 全论文池 Card 批处理控制实施记录

日期：2026-08-03

## 背景

全论文池清点后仍有数百篇 PDF 需要 MinerU 转换和 Card 生成。旧实现按顺序执行，只有最终输出可用于续跑，不适合长时间任务。本轮只改造 PDF/MD 到 Card 准备阶段，不启动跨论文综合，也不改变 A2 主写作链和 B2 储备能力的定位。

## 状态边界

单篇论文仍是最小发布单元。批次只调度论文，不跨论文合并解析任务。

```text
论文池清点快照
  -> 有界并发调度
  -> 单篇 MinerU / Markdown / Card
  -> 单篇 generation 发布
  -> 原子追加批次检查点
  -> 批次报告
```

批次状态：

- `running`：批次正在提交或等待在途论文。
- `paused`：收到暂停请求，不再启动新论文；已在途论文收束并记账。
- `cancelled`：收到取消请求，行为与暂停相同，但表达终止当前批次的意图。
- `completed`：清点账本中的全部待制卡论文均已完成。
- `completed_partial`：本批选择项全部成功，但清点账本仍有待处理论文。
- `completed_with_failures`：本批至少一篇失败；其他成功结果保持有效。
- 硬中断时 manifest 可停留在 `running`，续跑显式读取逐篇检查点，不把未记账产物猜测为成功。

## 技术实现

### 受控并发

- `--max-workers` 控制最大在途论文数，CLI 默认 2，硬上限 8。
- `--min-start-interval-seconds` 控制相邻论文启动间隔，CLI 默认 1 秒。
- 调度器只维持最多 `max_workers` 个 Future，不会一次把数百篇全部提交到线程池。
- MinerU 网络转换和结构解析可并发；材料 current、单篇 run、全局 corpus 和 review pack 的发布位于同一个进程内锁中。

### 协作式暂停与取消

运行时使用独立命令写入原子控制文件：

```powershell
python main.py paper-pool-card-control `
  --workspace workspace `
  --run-id <run-id> `
  --action pause
```

控制请求不强杀 MinerU HTTP 请求。已启动论文继续收束，不再启动新论文，避免产生“外部服务已完成但本地没有结果”的不确定状态。

### 检查点与续跑

- `runs/card_runs.jsonl` 在每篇完成后原子重写。
- 新批次使用 `--resume-from-run-id` 时优先读取检查点，即使旧批次没有最终 output 也可续跑。
- 只复用 `completed` 结果。
- 复用前校验 record ID、content ID、工作区 ID 和当前 generation。
- 来源文件在实际执行前重新校验大小和 SHA-256；与清点快照不一致时明确失败。
- 失败、未启动和未记账论文不会被当作完成，也不会生成猜测性结果。

## 真实验证

批次：`paper-pool-card-preparation-836files-20260803-concurrency-smoke`

- 输入清点：`paper-pool-836files-20260802-v2`
- 真实 PDF：2 篇
- 最大并发：2
- 启动间隔：2 秒
- 结果：2 成功、0 失败
- 运行时间：约 18.5 秒
- 两篇均写入逐篇检查点，最终 corpus 同时包含两篇。

重复并发测试曾捕捉到 Windows 下 `run.json` 替换与 corpus 扫描之间的竞态。锁边界扩大到完整发布阶段后，真实 Markdown 并发测试连续运行 20 次均通过。

## 验证中发现的解析问题

`三峡-葛洲坝两坝联合调度数学模型及算法.pdf` 的 MinerU Markdown 有 226 行，但旧范围选择在中文 H1 后遇到英文 H1 即终止，最初只生成 1 张摘要 Card。

本轮增加通用结构判断：

1. 第一 H1 段包含摘要但没有正文标题。
2. 相邻 H1 段再次包含摘要。
3. 相邻段包含二级及以下正文标题。

同时满足时，将相邻 H1 视为双语副题名并合并范围；若第一段已经有正文标题，则不合并，避免把两篇文章拼接。

真实重跑结果：

- 选定范围：第 3 行至第 226 行。
- block：1 增至 62。
- Card：1 增至 20。
- 三层覆盖率均为 1.0。
- 记录问题代码：`parse.bilingual_title_scope_merged`、`parse.unclassified_front_matter`。

新清点账本 `paper-pool-836files-20260803-v3`：

- 来源文件：836
- 唯一内容：834
- Card 就绪：48
- 待制卡：782
- 规范来源阻断：4
- 重复别名：2

增量主题筛选 `paper-pool-screening-836files-20260803-v6` 已复用原有 46 篇结果，并完成新增 2 篇：

- `三峡(围堰发电期)—葛洲坝梯级调度规程编制.pdf`：`supporting`
- `三峡-葛洲坝两坝联合调度数学模型及算法.pdf`：`core`
- 当前已筛选：48
- 筛选失败：0
- 待筛选但已有 Card：0
- 待制卡：782

## 当前限制

- 控制是协作式的，不会中止已经提交给 MinerU 的论文。
- 进程内锁不能协调两个独立 Python 进程同时写同一个 workspace；同一 workspace 只应运行一个 Card 批次。
- 续跑不会认领“单篇产物已出现但尚未写入检查点”的结果，此类论文会重新执行。
- 782 篇尚未实际批量运行；外部服务配额、长期速率和失败分布仍需分批观察。

## 下一步

使用每批 10 至 20 篇、最大并发 2 的保守参数推进。每批完成后发布新 inventory，再对新增 Card 执行单篇主题筛选。累计观察 MinerU 错误率和耗时后，再决定是否提高并发上限。
