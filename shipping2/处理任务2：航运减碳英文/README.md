# 处理任务2：航运减碳英文 - 文献质评项目

## 项目概述

本项目用于对英文文献（Web of Science）进行质量评估，筛选与三峡枢纽航运相关的文献。系统通过调用硅基流动API（DeepSeek V3.2模型）分析文献内容，评估其与三个研究方向的关联程度。

## 研究方向

| 方向 | 名称 | 描述 |
|------|------|------|
| direction_1 | 经济社会效益 | 三峡枢纽航运功能对经济社会发展的贡献，包括航运效益、区域经济发展、黄金水道建设等 |
| direction_2 | 节能减排 | 三峡枢纽航运在交通物流领域的节能减排贡献，水运碳减排、绿色航运等 |
| direction_3 | 水运通过能力 | 三峡枢纽水运通过能力及潜力挖掘，包括船闸通航能力、优化调度等 |

## 文件结构

```
处理任务2：航运减碳英文/
├── savedrecs.txt          # 原始WOS导出的文献题录
├── savedrecs (2).txt      # 分卷2
├── savedrecs (3).txt      # 分卷3
├── config.json            # 配置文件（需手动配置API）
├── process_state.json     # 处理状态记录
├── convert_wos.py         # WOS格式转换脚本
├── process_papers.py      # 文献质评脚本（调用API评估）
├── restore_results.py     # 恢复质评结果脚本
└── output/                # 输出目录
    ├── 0001_xxx.md        # 转换后的文献MD文件
    ├── ...
    └── index.md           # 文献索引
```

## 快速开始

### 1. 配置API密钥

首次运行前，需要在 `config.json` 中配置API信息：

```json
{
  "api_key": "你的硅基流动API密钥",
  "model": "deepseek-ai/DeepSeek-V3",
  "api_url": "https://api.siliconflow.cn/v1/chat/completions",
  "input_dir": "output",
  "output_dir": "output",
  "state_file": "process_state.json",
  "score_threshold": 60,
  "max_workers": 5,
  "max_retries": 2,
  "retry_delay": 10,
  "directions": {
    "direction_1": {
      "name": "经济社会效益",
      "description": "三峡枢纽航运功能对经济社会发展的贡献，包括：航运经济效益（货运量、运输成本、区域经济发展）、社会效益（就业、通航安全、民生改善）、黄金水道建设等"
    },
    "direction_2": {
      "name": "节能减排",
      "description": "三峡枢纽航运在交通物流领域的节能减排贡献，包括：水运相比公路/铁路的碳减排、船舶运输能效提升、绿色航运、多式联运减排等"
    },
    "direction_3": {
      "name": "水运通过能力",
      "description": "三峡枢纽的水运通过能力以及潜力挖掘方式和进展，包括：船闸通航能力/效率、船闸/通航管理、通航调度、优化调度、货运量提升空间、扩能改造、航道整治等"
    }
  }
}
```

### 2. 运行步骤

```bash
# 步骤1：转换文献格式（WOS → Markdown）
python convert_wos.py

# 步骤2：运行文献质评
python process_papers.py
```

### 3. 查看结果

质评结果输出到任务根目录：
- `三峡枢纽航运经济社会效益相关文献汇总_质评版.md`
- `三峡枢纽航运节能减排相关文献汇总_质评版.md`
- `三峡枢纽水运通过能力相关文献汇总_质评版.md`

## 评分标准

- **入门分数**: ≥60分
- **评分构成**:
  - 相关性得分（50%）: 完全相关=50分，部分相关=25分，不相关=0分
  - 引用次数得分（20%）: 50次以上=20分，20-49次=15分，10-19次=12分，5-9次=8分
  - 摘要质量得分（15%）: 完整有方法数据=15分，简短=10分，无/极简=5分
  - 年份得分（15%）: 2016-2025=15分，2011-2015=10分

## 恢复质评结果

如果结果文件被覆盖但状态文件（`process_state.json`）仍存在：

```bash
python restore_results.py
```

此脚本会从 `process_state.json` 读取已有的评分数据（1765条记录），重新生成质评结果文件。

**注意**: 恢复模式下无法获取原始API评估的"相关性说明"，会显示"(从状态文件恢复)"。

## 数据来源

- **输入**: Web of Science 导出的题录文件（savedrecs.txt）
- **格式**: WOS标准格式，每条记录以 `ER` 标记结束

## 与处理任务1的区别

| 对比项 | 处理任务1（中文） | 处理任务2（英文） |
|--------|-------------------|-------------------|
| 数据来源 | CNKI导出 | Web of Science |
| 转换脚本 | convert_citations.py | convert_wos.py |
| 使用模型 | Qwen/Qwen3-8B | DeepSeek V3 |
| 文献数量 | ~100篇 | ~1765篇 |

## 依赖

- Python 3.8+
- requests

## 注意事项

1. 确保API密钥有效且余额充足（英文任务文献量较大）
2. 状态文件 `process_state.json` 记录所有处理进度，删除后需重新运行
3. `failed_api_calls.json` 记录失败的API调用，可用于重试
4. 建议首次运行时设置较小的 `max_workers` 以避免API限流