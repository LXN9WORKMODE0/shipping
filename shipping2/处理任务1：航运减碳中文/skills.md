# 文献题录处理与分析技能

## 概述

将CNKI/学术数据库导出的题录TXT文件，通过自动化流程转换为结构化Markdown文件，并利用LLM API进行分类分析和质量评估。

## 工作流程

```
输入文件(TXT) → 解析 → Markdown批量生成 → LLM分类分析 → 质量评分 → 汇总输出
```

---

## 技能1: 题录TXT转Markdown

### 脚本
[convert_citations.py](处理任务1：航运减碳中文/convert_citations.py)

### 输入
- CNKI导出的题录TXT文件 (`导出题录.txt`)

### 核心函数

```python
def parse_citations(text):
    """按空行分割，提取每条题录"""

def parse_citation_block(block):
    """解析单条题录，匹配字段 {FieldName}: value"""

def is_complete_citation(citation):
    """检查必要字段: Author, Year, Title, Journal, Abstract"""

def generate_md_content(citation):
    """生成Markdown格式内容"""

def sanitize_filename(title):
    """生成安全的文件名"""
```

### 配置
- `INPUT_FILE`: 输入TXT文件名
- `OUTPUT_DIR`: 输出目录名 (默认 "output")

### 输出
- `output/` 目录下每个题录一个MD文件
- `output/index.md` 汇总索引

---

## 技能2: 文献LLM分类分析

### 脚本
[process_papers.py](处理任务1：航运减碳中文/process_papers.py)

### 配置 (config.json)
```json
{
    "api_key": "your-api-key",
    "model": "deepseek-ai/DeepSeek-V3",
    "api_url": "https://api.siliconflow.cn/v1/chat/completions",
    "input_dir": "./output",
    "output_dir": "./results",
    "state_file": "./state.json",
    "score_threshold": 60,
    "max_workers": 5,
    "max_retries": 2,
    "retry_delay": 10,
    "directions": ["航运经济社会效益", "航运节能减排", "水运通过能力"]
}
```

### 核心函数

```python
def call_api(paper_info, direction):
    """调用LLM API分析论文相关性"""

def parse_api_response(response_text):
    """解析API返回的分类结果"""

def parse_md_file(file_path):
    """读取Markdown文件提取题录信息"""

def calculate_score(paper_info):
    """多维度质量评分:
    - 年份评分 (25%): 2016-2025=25分, 2011-2015=15分
    - 文献类型 (20%): 学位论文=20, 核心期刊=20
    - 摘要质量 (15%): >200字=15分
    """

def process_single_file(file_path, direction_1, direction_2, direction_3):
    """单文件处理: 读取→API分析→评分→写入结果"""
```

### 多线程处理
- 使用 `ThreadPoolExecutor` 并发处理
- 线程锁保护共享资源 (state_lock, results_lock)
- 支持断点续传 (state.json)

### 输出
- 按方向分别生成汇总MD文件
- `failed_api_calls.json` 记录失败重试

---

## 复用方法

### 新项目使用步骤

1. **准备输入数据**
   - 从CNKI导出题录为TXT格式 (选择EndNote格式或自定义字段)
   - 确保包含: Author, Year, Title, Journal, Abstract

2. **修改配置**
   - 复制 `convert_citations.py` 到新项目目录
   - 修改 `INPUT_FILE` 为实际文件名

3. **运行转换**
   ```bash
   python convert_citations.py
   ```

4. **配置API**
   - 复制 `process_papers.py` 和 `config.json`
   - 修改 `api_key`, `directions` 等配置

5. **运行分析**
   ```bash
   python process_papers.py
   ```

---

## 关键实现细节

### 题录格式解析
- 字段格式: `{FieldName}: value`
- 空行分隔不同记录
- 支持中文字段名

### API Prompt设计
```
请分析以下文献是否属于[方向]相关研究，返回JSON格式:
{"relevant": true/false, "score": 0-100, "reason": "原因"}
```

### 评分机制
- LLM判断内容相关性 (40%)
- 年份评分 (25%)
- 文献类型 (20%)
- 摘要质量 (15%)

---

## 文件结构

```
project/
├── 导出题录.txt           # 原始数据
├── convert_citations.py   # 转换脚本
├── process_papers.py      # 分析脚本
├── config.json            # 配置文件
├── output/                # 转换结果
│   ├── 0001_xxx.md
│   └── index.md
└── results/               # 分析结果
    ├── xxx相关文献汇总_质评版.md
    └── failed_api_calls.json
```