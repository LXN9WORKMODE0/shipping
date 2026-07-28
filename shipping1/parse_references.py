#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
解析并分析"导出题录.txt"中的文献题录
筛选与三峡船闸通航减碳相关的文献
"""

import os
import re
from pathlib import Path
from collections import defaultdict

# 定义目录
INPUT_FILE = Path(r"C:\Users\DELL\Desktop\shipping\导出题录.txt")
OUTPUT_DIR = Path(r"C:\Users\DELL\Desktop\shipping\literature_output")

# 减碳相关关键词
CARBON_KEYWORDS = [
    '碳', '节能', '能耗', '能源', '低碳', '碳排放', '碳减排', '碳中和', '碳足迹',
    '绿色', '环保', '清洁能源', '岸电', 'LNG', '电动', '减排', '可持续',
    '多式联运', '公铁水', '铁水联运', '运输方式', '运输结构',
    '航运效率', '船舶大型化', '船型优化', '翻坝', '运输成本',
    '物流成本', '单位运输', '周转量', '运输周转'
]

# 需要排除的文献类型
EXCLUDE_KEYWORDS = [
    '人民日报', '报道研究', '新闻报道', '政府公报', '通告', '纪实',
    '文学', '小说', '叙事', '文化', '风景', '旅游', '历史研究',
    '人物', '传记', '评述', '综述', '考古', '建筑学', '城市规划'
]

# 主题分类框架
THEME_CATEGORIES = {
    "B": {"name": "通航效率优化", "subthemes": {
        "B1": "调度优化", "B2": "船型标准化", "B3": "信息化建设",
        "B4": "翻坝转运", "B5": "通过能力提升"
    }},
    "C": {"name": "碍航问题", "subthemes": {
        "C1": "拥堵分析", "C2": "碍航损失评估", "C3": "自然条件制约"
    }},
    "D": {"name": "经济社会贡献", "subthemes": {
        "D1": "运输成本", "D2": "区域经济", "D3": "综合交通"
    }},
    "E": {"name": "交通减碳关联", "subthemes": {
        "E1": "水运能耗优势", "E2": "船舶节能技术", "E3": "清洁能源",
        "E4": "多式联运", "E5": "流量调度影响"
    }},
    "F": {"name": "其他相关", "subthemes": {}}
}

# 关键词到主题的映射
KEYWORD_THEME_MAP = {
    # 通航效率优化
    "调度": "B1", "优化": "B1", "协同": "B1", "排档": "B1",
    "船型": "B2", "标准化": "B2", "船舶大型化": "B2",
    "信息化": "B3", "系统": "B3", "监控": "B3", "管理": "B3",
    "翻坝": "B4", "转运": "B4",
    "通过能力": "B5", "过闸能力": "B5", "通航能力": "B5",

    # 碍航问题
    "拥堵": "C1", "积压": "C1", "待闸": "C1", "延误": "C1",
    "碍航": "C2", "损失": "C2", "经济损失": "C2",
    "泥沙": "C3", "水位": "C3", "地形": "C3", "河道": "C3",

    # 经济社会贡献
    "成本": "D1", "经济": "D1", "效益": "D1",
    "区域": "D2", "发展": "D2", "经济带": "D2",
    "综合交通": "D3", "运输方式": "D3", "公铁水": "D3",

    # 交通减碳关联
    "节能": "E2", "能耗": "E2", "能源": "E2", "燃油": "E2",
    "碳": "E1", "低碳": "E1", "减排": "E1", "环保": "E1",
    "LNG": "E3", "清洁能源": "E3", "电动": "E3", "岸电": "E3",
    "多式联运": "E4", "铁水联运": "E4", "公铁水": "E4",
    "流量": "E5", "下泄流量": "E5", "出库流量": "E5",
}


def parse_references(filepath):
    """解析题录文件"""
    references = []

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"读取文件失败: {e}")
        return references

    # 按空行分隔各条文献
    entries = content.split('\n\n')

    for entry in entries:
        if not entry.strip() or '{Reference Type}' not in entry:
            continue

        ref = {}

        # 提取各字段
        ref_type_match = re.search(r'\{Reference Type\}:\s*(.+)', entry)
        ref['type'] = ref_type_match.group(1).strip() if ref_type_match else ""

        author_match = re.search(r'\{Author\}:\s*(.+)', entry)
        ref['author'] = author_match.group(1).strip() if author_match else ""

        year_match = re.search(r'\{Year\}:\s*(\d{4})', entry)
        ref['year'] = year_match.group(1).strip() if year_match else ""

        title_match = re.search(r'\{Title\}:\s*(.+)', entry)
        ref['title'] = title_match.group(1).strip() if title_match else ""

        url_match = re.search(r'\{URL\}:\s*(.+)', entry)
        ref['url'] = url_match.group(1).strip() if url_match else ""

        journal_match = re.search(r'\{Journal\}:\s*(.+)', entry)
        ref['journal'] = journal_match.group(1).strip() if journal_match else ""

        keywords_match = re.search(r'\{Keywords\}:\s*(.+)', entry)
        ref['keywords'] = keywords_match.group(1).strip() if keywords_match else ""

        abstract_match = re.search(r'\{Abstract\}:\s*(.+)', entry, re.DOTALL)
        ref['abstract'] = abstract_match.group(1).strip() if abstract_match else ""

        if ref['title']:
            references.append(ref)

    return references


def filter_and_score(references):
    """关键词匹配与评分"""
    matched = []

    for ref in references:
        title = ref.get('title', '')
        keywords = ref.get('keywords', '')
        abstract = ref.get('abstract', '')
        full_text = title + keywords + abstract

        # 检查是否需要排除
        should_exclude = False
        for excl in EXCLUDE_KEYWORDS:
            if excl in title:
                should_exclude = True
                break

        if should_exclude:
            continue

        # 检查关键词匹配
        matched_keywords = []
        for kw in CARBON_KEYWORDS:
            if kw in full_text:
                matched_keywords.append(kw)

        if not matched_keywords:
            continue

        # 内容质量评估
        # 1. 研究深度评估
        research_depth = 1
        if len(abstract) > 300:
            research_depth = 3
        elif len(abstract) > 150:
            research_depth = 2
        if any(x in abstract for x in ['模型', '算法', '分析', '研究', '方法', '数据', '实证']):
            research_depth = min(5, research_depth + 1)
        if any(x in abstract for x in ['创新', '提出', '构建', '建立']):
            research_depth = min(5, research_depth + 1)

        # 2. 研究价值评估
        research_value = 1
        if len(abstract) > 200:
            research_value = 3
        elif len(abstract) > 100:
            research_value = 2
        if any(x in abstract for x in ['优化', '提高', '降低', '减少', '提升', '改善']):
            research_value = min(5, research_value + 1)
        if any(x in abstract for x in ['对策', '建议', '方案', '策略']):
            research_value = min(5, research_value + 1)

        # 3. 减碳关联度评估
        carbon_relevance = 1
        direct_carbon_terms = ['碳', '节能', '能耗', '低碳', '碳排放', '碳减排', '碳中和', '岸电', 'LNG', '清洁能源']
        if any(x in full_text for x in direct_carbon_terms):
            carbon_relevance = 5
        elif any(x in full_text for x in ['多式联运', '公铁水', '铁水联运', '船舶大型化', '航运效率']):
            carbon_relevance = 3
        elif any(x in full_text for x in ['运输成本', '物流成本', '运输结构', '运输方式']):
            carbon_relevance = 3

        content_quality = research_depth + research_value

        # 评分等级
        if content_quality >= 4 and carbon_relevance >= 4:
            relevance_level = "高相关"
        elif content_quality >= 2 and carbon_relevance >= 3:
            relevance_level = "中相关"
        else:
            relevance_level = "低相关/不相关"

        ref['matched_keywords'] = matched_keywords
        ref['research_depth'] = research_depth
        ref['research_value'] = research_value
        ref['carbon_relevance'] = carbon_relevance
        ref['content_quality'] = content_quality
        ref['relevance_level'] = relevance_level

        matched.append(ref)

    return matched


def classify_by_theme(references):
    """按主题分类"""
    classified = defaultdict(list)

    for ref in references:
        title = ref.get('title', '')
        abstract = ref.get('abstract', '')
        keywords = ref.get('keywords', '')
        full_text = title + keywords + abstract

        # 查找匹配的主题
        matched_themes = set()
        for kw, theme in KEYWORD_THEME_MAP.items():
            if kw in full_text:
                matched_themes.add(theme)

        if matched_themes:
            # 优先选择E类（交通减碳关联），其次B类（通航效率）
            if 'E1' in matched_themes or 'E2' in matched_themes or 'E3' in matched_themes or 'E4' in matched_themes or 'E5' in matched_themes:
                ref['theme'] = 'E'
                ref['theme_name'] = '交通减碳关联'
            elif 'B1' in matched_themes or 'B2' in matched_themes or 'B3' in matched_themes or 'B4' in matched_themes or 'B5' in matched_themes:
                ref['theme'] = 'B'
                ref['theme_name'] = '通航效率优化'
            elif 'C1' in matched_themes or 'C2' in matched_themes or 'C3' in matched_themes:
                ref['theme'] = 'C'
                ref['theme_name'] = '碍航问题'
            elif 'D1' in matched_themes or 'D2' in matched_themes or 'D3' in matched_themes:
                ref['theme'] = 'D'
                ref['theme_name'] = '经济社会贡献'
            else:
                ref['theme'] = 'F'
                ref['theme_name'] = '其他相关'
        else:
            ref['theme'] = 'F'
            ref['theme_name'] = '其他相关'

        classified[ref['theme']].append(ref)

    return classified


def generate_report(references, classified):
    """生成分析报告"""
    # 统计
    total = len(references)
    high_relevance = [r for r in references if r['relevance_level'] == '高相关']
    mid_relevance = [r for r in references if r['relevance_level'] == '中相关']
    low_relevance = [r for r in references if r['relevance_level'] == '低相关/不相关']

    report = f"""# 三峡船闸通航对交通领域减碳贡献相关文献分析报告

## 一、数据处理概况

### 1.1 原始数据

| 项目 | 数量 |
|------|------|
| 题录文件 | 导出题录.txt |
| 原始文献数 | {total} |
| 关键词匹配数 | {total} |

### 1.2 筛选结果

| 相关等级 | 数量 |
|----------|------|
| 高相关 | {len(high_relevance)} |
| 中相关 | {len(mid_relevance)} |
| 低相关/不相关 | {len(low_relevance)} |
| **总计** | **{total}** |

### 1.3 筛选标准

#### 关键词匹配
- **碳排放相关**: 碳、节能、能耗、能源、低碳、碳排放、碳减排、碳中和、碳足迹
- **绿色航运**: 绿色、环保、清洁能源、岸电、LNG、电动船舶
- **运输优化**: 多式联运、公铁水联运、铁水联运、船舶大型化、航运效率

#### 排除类型
- 新闻报道、政策通告、报纸研究
- 纯文学/历史研究、无实质研究内容

#### 评分等级
- **高相关**: 内容质量>=4分 且 减碳关联度>=4分
- **中相关**: 内容质量>=2分 且 减碳关联度>=3分

---

## 二、主题分类统计

"""

    # 按主题分类统计
    theme_stats = {}
    for theme, refs in classified.items():
        theme_name = THEME_CATEGORIES.get(theme, {}).get('name', '其他相关')
        high = sum(1 for r in refs if r['relevance_level'] == '高相关')
        mid = sum(1 for r in refs if r['relevance_level'] == '中相关')
        theme_stats[theme] = {
            'name': theme_name,
            'total': len(refs),
            'high': high,
            'mid': mid
        }
        report += f"### {theme_name} ({len(refs)}篇)\n"
        report += f"- 高相关: {high} 篇 | 中相关: {mid} 篇\n\n"

    # 高相关文献列表
    if high_relevance:
        report += "---\n\n## 三、高相关文献 ({})\n\n".format(len(high_relevance))
        for i, ref in enumerate(high_relevance, 1):
            report += f"### {i}. {ref['title']}\n\n"
            report += f"- **作者**: {ref.get('author', 'N/A')}\n"
            report += f"- **年份**: {ref.get('year', 'N/A')}\n"
            report += f"- **文献类型**: {ref.get('type', 'N/A')}\n"
            report += f"- **期刊/会议**: {ref.get('journal', 'N/A')}\n"
            report += f"- **匹配关键词**: {', '.join(ref.get('matched_keywords', []))}\n"
            report += f"- **研究深度**: {ref.get('research_depth', 0)}/5 | **研究价值**: {ref.get('research_value', 0)}/5 | **减碳关联度**: {ref.get('carbon_relevance', 0)}/5\n"
            report += f"- **主题分类**: {ref.get('theme_name', '其他相关')}\n\n"

            abstract = ref.get('abstract', '')
            if abstract:
                report += f"**摘要**: {abstract[:300]}"
                if len(abstract) > 300:
                    report += "..."
                report += "\n"

            report += "\n---\n\n"

    # 中相关文献列表
    if mid_relevance:
        report += "---\n\n## 四、中相关文献 ({})\n\n".format(len(mid_relevance))
        for i, ref in enumerate(mid_relevance, 1):
            report += f"### {i}. {ref['title']}\n\n"
            report += f"- **作者**: {ref.get('author', 'N/A')}\n"
            report += f"- **年份**: {ref.get('year', 'N/A')}\n"
            report += f"- **匹配关键词**: {', '.join(ref.get('matched_keywords', []))}\n"
            report += f"- **主题分类**: {ref.get('theme_name', '其他相关')}\n\n"
            report += "\n---\n\n"

    report += f"""---

*报告生成时间: 2026-03-02*
*分析文献总数: {total}篇*
*高相关文献: {len(high_relevance)}篇*
*中相关文献: {len(mid_relevance)}篇*
"""

    return report


def main():
    print("=" * 60)
    print("开始分析导出题录.txt")
    print("=" * 60)

    # 1. 解析题录数据
    print("\n[1/4] 解析题录数据...")
    references = parse_references(INPUT_FILE)
    print(f"共解析 {len(references)} 条文献")

    # 2. 关键词匹配与评分
    print("\n[2/4] 关键词匹配与评分...")
    matched_references = filter_and_score(references)
    print(f"关键词匹配: {len(matched_references)} 条")

    # 3. 主题分类
    print("\n[3/4] 主题分类...")
    classified = classify_by_theme(matched_references)
    for theme, refs in classified.items():
        theme_name = THEME_CATEGORIES.get(theme, {}).get('name', '其他相关')
        print(f"  - {theme_name}: {len(refs)} 篇")

    # 4. 生成报告
    print("\n[4/4] 生成分析报告...")
    report = generate_report(matched_references, classified)

    # 确保输出目录存在
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 保存报告
    report_path = OUTPUT_DIR / "analysis_summary_new.md"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f"\n分析报告已保存到: {report_path}")

    # 打印统计摘要
    high = sum(1 for r in matched_references if r['relevance_level'] == '高相关')
    mid = sum(1 for r in matched_references if r['relevance_level'] == '中相关')
    print("\n" + "=" * 60)
    print("统计摘要")
    print("=" * 60)
    print(f"原始文献数: {len(references)}")
    print(f"关键词匹配: {len(matched_references)}")
    print(f"高相关: {high}")
    print(f"中相关: {mid}")
    print(f"低相关/不相关: {len(matched_references) - high - mid}")

    return matched_references


if __name__ == "__main__":
    main()
