# 三峡枢纽航运文献调研工具

[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

自动化外文学术文献检索与下载工具，专门针对三峡枢纽航运相关研究领域。

## 功能特性

- **多平台学术搜索**：支持 OpenAlex、Semantic Scholar、Google Scholar 三大学术搜索平台
- **DOI 智能解析**：自动解析文献 DOI 并获取 PDF 下载链接
- **批量下载**：支持批量下载文献 PDF 文件
- **去重机制**：智能合并多平台结果，自动去除重复文献
- **无时间限制**：覆盖所有历史文献，不限制发表年份
- **结果保存**：支持 txt 格式输出和 JSON 下载记录

## 研究方向

本工具针对三峡枢纽航运的三个核心研究方向：

| 方向 | 名称 | 描述 |
|------|------|------|
| direction_1 | 经济社会效益 | 航运经济效益、区域经济、长江经济带发展、腹地经济 |
| direction_2 | 节能减排 | 碳排放对比、模态转换、绿色航运、温室气体减排 |
| direction_3 | 水运通过能力 | 船闸通航能力、拥堵缓解、调度优化、扩能改造 |

## 项目结构

```
shipping3/
├── config.py                       # 配置文件
├── main.py                         # 命令行入口
├── scholar_search.py               # Google Scholar 搜索模块（原始）
├── doi_resolver.py                 # DOI 解析模块
├── pdf_downloader.py               # PDF 下载模块
├── research_directions.py          # 研究方向主程序
├── platform_searchers/             # 多平台搜索适配器
│   ├── __init__.py
│   ├── base.py                     # 抽象基类和 Paper 数据模型
│   ├── semantic_searcher.py        # Semantic Scholar 适配器
│   ├── openalex_searcher.py        # OpenAlex 适配器
│   ├── google_scholar_searcher.py  # Google Scholar 适配器
│   └── unified_searcher.py         # 统一搜索引擎
├── papers/                         # 文献保存目录
│   ├── direction_1/                # 经济社会效益
│   ├── direction_2/                # 节能减排
│   └── direction_3/                # 水运通过能力
└── requirements.txt                # 依赖列表
```

## 安装依赖

```bash
pip install -r requirements.txt
```

**主要依赖**：
- `requests` - HTTP 请求库
- `beautifulsoup4` - HTML 解析
- `tqdm` - 进度条
- `scholarly` - Google Scholar 搜索（可选）

## 使用方法

### 基本命令

```bash
# 查看所有研究方向
python research_directions.py --list

# 搜索特定方向并下载 PDF
python research_directions.py -d 1 --download
python research_directions.py -d 2 --download
python research_directions.py -d 3 --download

# 搜索所有方向
python research_directions.py -d all

# 指定返回结果数量
python research_directions.py -d 1 -m 50
```

### 参数说明

| 参数 | 说明 |
|------|------|
| `-d, --direction` | 研究方向编号 (1/2/3/all) |
| `-m, --max` | 最大返回结果数（默认30） |
| `--download` | 是否下载 PDF 文件 |
| `--list` | 列出所有研究方向 |

### 高级用法

```bash
# 只搜索不下载
python research_directions.py -d 1 -m 100

# 下载前20篇 PDF
python research_directions.py -d 1 -m 20 --download
```

## 技术架构

### 多平台搜索适配器

采用适配器模式，统一的 `SearchPlatform` 抽象基类：

```python
class SearchPlatform(ABC):
    @abstractmethod
    def search(
        self,
        query: str,
        limit: int = 50,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None
    ) -> List[Paper]:
        pass
```

### 结果去重策略

1. **DOI 去重**：首先用 DOI 进行精确去重
2. **标题去重**：对无 DOI 的文献，使用标准化标题去重
3. **来源优先级**：Semantic Scholar > OpenAlex > Google Scholar

### 平台 API

| 平台 | API 端点 | 请求限制 |
|------|----------|----------|
| OpenAlex | api.openalex.org/works | 非常宽松 |
| Semantic Scholar | api.semanticscholar.org | 100次/分钟 |
| Google Scholar | scholarly 库 | 需要代理 |

## 配置文件

编辑 `config.py` 自定义配置：

```python
# 统一搜索配置
UNIFIED_SEARCH_CONFIG = {
    "enabled_platforms": ["openalex", "google_scholar", "semantic_scholar"],
    "max_results_per_platform": 50,
    "total_limit": 300,
    "deduplication_enabled": True,
}

# 代理设置（如需要）
PROXY_CONFIG = {
    "enabled": False,
    "http_proxy": "",
    "https_proxy": "",
}
```

## 输出示例

```
======================================================================
📚 调研方向: 经济社会效益
📋 描述: 三峡枢纽航运功能对经济社会发展的贡献...
======================================================================

   可用平台: openalex, google_scholar, semantic_scholar

🔍 搜索关键词: Three Gorges Dam
   找到 50 篇文献

📊 共找到 483 篇不重复的文献（含多个平台）

✅ 全部调研完成！
📁 文献保存在: C:\...\papers\direction_1\
```

## 注意事项

1. **API 速率限制**：Semantic Scholar 有请求频率限制，工具会自动处理 429 错误并重试
2. **网络问题**：部分文献可能因网络原因下载失败，这是正常现象
3. **DOI 解析**：只有包含有效 DOI 的文献才能下载 PDF
4. **学术诚信**：请合理使用，遵守各平台的 Terms of Service

## 拓展开发

### 添加新的搜索平台

1. 在 `platform_searchers/` 目录下创建新的适配器类
2. 继承 `SearchPlatform` 抽象基类
3. 实现 `search()` 方法和 `platform_name` 属性
4. 在 `config.py` 的 `enabled_platforms` 中添加平台名称

```python
from .base import SearchPlatform, Paper

class CustomSearcher(SearchPlatform):
    @property
    def platform_name(self) -> str:
        return "custom_platform"

    def search(self, query: str, limit: int = 50, ...) -> List[Paper]:
        # 实现搜索逻辑
        pass
```

## 许可证

MIT License

## 作者

本工具专为三峡枢纽航运研究设计，用于自动化文献调研。