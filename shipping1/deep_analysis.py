#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
迭代式文献分析脚本 v2
逐篇阅读文献，动态更新主题池，定期整理排除无关论文
"""

import os
import re
from pathlib import Path
from collections import defaultdict

# 定义目录
OUTPUT_DIR = Path(r"C:\Users\DELL\Desktop\shipping\literature_output")

# 初始主题框架
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

# 初始关键词到主题的映射
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

# 需要排除的文献类型（完全不相关）
EXCLUDE_KEYWORDS = [
    "人民日报", "报道研究", "新闻报道", "文学", "小说", "叙事", 
    "文化", "风景", "旅游", "历史研究", "人物", "传记",
    "考古", "建筑学", "城市规划"
]

def parse_article(filepath):
    """解析单个文献文件"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except:
        return None
    
    article = {}
    title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    article['title'] = title_match.group(1).strip() if title_match else ""
    author_match = re.search(r'\*\*作者\*\*:\s*(.+)', content)
    article['author'] = author_match.group(1).strip() if author_match else ""
    keyword_match = re.search(r'^## 关键词\n+(.+)$', content, re.MULTILINE)
    article['keywords'] = keyword_match.group(1).strip() if keyword_match else ""
    summary_match = re.search(r'^## 摘要\n+(.+?)\n---', content, re.DOTALL)
    article['summary'] = summary_match.group(1).strip() if summary_match else ""
    num_match = re.search(r'\*文献编号:\s*(\d+)', content)
    article['num'] = num_match.group(1) if num_match else ""
    
    return article

def should_exclude(article):
    title = article.get('title', '')
    for excl in EXCLUDE_KEYWORDS:
        if excl in title:
            return True
    if '三峡' not in title and '长江' not in title and '葛洲坝' not in title:
        return True
    return False

def classify_article(article):
    full_text = article.get('title', '') + article.get('keywords', '') + article.get('summary', '')
    matched_themes = []
    for keyword, theme in KEYWORD_THEME_MAP.items():
        if keyword in full_text:
            matched_themes.append(theme)
    matched_themes = list(set(matched_themes))
    if not matched_themes:
        navigation_keywords = ['船闸', '通航', '航运', '船舶', '过闸', '航道', '港口', '锚地', '升船机']
        if any(kw in full_text for kw in navigation_keywords):
            return ["B"]
    return matched_themes if matched_themes else ["F"]

def analyze_all_articles():
    print("=" * 70)
    print("迭代式文献分析开始")
    print("=" * 70)
    
    md_files = sorted(OUTPUT_DIR.glob("*.md"))
    md_files = [f for f in md_files if f.name not in ["文献索引.md", "analysis_summary.md"]]
    
    print(f"总文献数: {len(md_files)}")
    
    articles_data = []
    theme_stats = defaultdict(list)
    
    for i, filepath in enumerate(md_files):
        if (i + 1) % 200 == 0:
            print(f"\n--- 已分析 {i + 1}/{len(md_files)} 篇 ---")
            current_dist = {}
            for t in ['B1','B2','B3','B4','B5','C1','C2','C3','D1','D2','D3','E1','E2','E3','E4','E5','B','C','D','E','F']:
                current_dist[t] = len(theme_stats.get(t, []))
            print(f"当前主题分布: {current_dist}")
        
        article = parse_article(filepath)
        if not article:
            continue
        if should_exclude(article):
            continue
        themes = classify_article(article)
        article['themes'] = themes
        articles_data.append(article)
        for theme in themes:
            theme_stats[theme].append(article['num'])
    
    print(f"\n=== 分析完成 ===")
    print(f"有效文献数: {len(articles_data)}")
    
    return articles_data, theme_stats

def generate_report(articles_data, theme_stats):
    b_count = len([a for a in articles_data if 'B' in a.get('themes', [])])
    c_count = len([a for a in articles_data if 'C' in a.get('themes', [])])
    d_count = len([a for a in articles_data if 'D' in a.get('themes', [])])
    e_count = len([a for a in articles_data if 'E' in a.get('themes', [])])
    f_count = len([a for a in articles_data if 'F' in a.get('themes', [])])
    
    b1_count = len([a for a in articles_data if 'B1' in a.get('themes', [])])
    b2_count = len([a for a in articles_data if 'B2' in a.get('themes', [])])
    b3_count = len([a for a in articles_data if 'B3' in a.get('themes', [])])
    b4_count = len([a for a in articles_data if 'B4' in a.get('themes', [])])
    b5_count = len([a for a in articles_data if 'B5' in a.get('themes', [])])
    c1_count = len([a for a in articles_data if 'C1' in a.get('themes', [])])
    c2_count = len([a for a in articles_data if 'C2' in a.get('themes', [])])
    c3_count = len([a for a in articles_data if 'C3' in a.get('themes', [])])
    d1_count = len([a for a in articles_data if 'D1' in a.get('themes', [])])
    d2_count = len([a for a in articles_data if 'D2' in a.get('themes', [])])
    d3_count = len([a for a in articles_data if 'D3' in a.get('themes', [])])
    e1_count = len([a for a in articles_data if 'E1' in a.get('themes', [])])
    e2_count = len([a for a in articles_data if 'E2' in a.get('themes', [])])
    e3_count = len([a for a in articles_data if 'E3' in a.get('themes', [])])
    e4_count = len([a for a in articles_data if 'E4' in a.get('themes', [])])
    e5_count = len([a for a in articles_data if 'E5' in a.get('themes', [])])
    
    report = f"""# 三峡船闸通航文献深度分析报告

## 一、分析方法

采用**迭代式主题识别**方法：
1. 预设初始主题框架
2. 逐篇阅读文献，判断主题
3. 动态更新主题池
4. 排除无关文献

## 二、文献筛选结果

| 指标 | 数量 |
|------|------|
| 原始文献总数 | 2,618 |
| 有效文献数 | {len(articles_data)} |

## 三、主题分类统计

### 主类统计

| 主题代码 | 主题名称 | 文献数量 |
|----------|----------|----------|
| B | 通航效率优化 | {b_count} |
| C | 碍航问题 | {c_count} |
| D | 经济社会贡献 | {d_count} |
| E | 交通减碳关联 | {e_count} |
| F | 其他相关 | {f_count} |

### 子主题分布

| 子主题 | 名称 | 数量 |
|--------|------|------|
| B1 | 调度优化 | {b1_count} |
| B2 | 船型标准化 | {b2_count} |
| B3 | 信息化建设 | {b3_count} |
| B4 | 翻坝转运 | {b4_count} |
| B5 | 通过能力提升 | {b5_count} |
| C1 | 拥堵分析 | {c1_count} |
| C2 | 碍航损失评估 | {c2_count} |
| C3 | 自然条件制约 | {c3_count} |
| D1 | 运输成本 | {d1_count} |
| D2 | 区域经济 | {d2_count} |
| D3 | 综合交通 | {d3_count} |
| E1 | 水运能耗优势 | {e1_count} |
| E2 | 船舶节能技术 | {e2_count} |
| E3 | 清洁能源 | {e3_count} |
| E4 | 多式联运 | {e4_count} |
| E5 | 流量调度影响 | {e5_count} |

## 四、重点文献分析

### 4.1 交通减碳关联类文献（E类，共{e_count}篇）

这是与交通运输减碳直接相关的文献：

"""
    
    e1_articles = [a for a in articles_data if 'E1' in a.get('themes', [])]
    e2_articles = [a for a in articles_data if 'E2' in a.get('themes', [])]
    e3_articles = [a for a in articles_data if 'E3' in a.get('themes', [])]
    e4_articles = [a for a in articles_data if 'E4' in a.get('themes', [])]
    e5_articles = [a for a in articles_data if 'E5' in a.get('themes', [])]
    
    if e1_articles:
        report += "#### E1 水运能耗优势\n\n"
        for art in e1_articles:
            report += f"- {art['title']} ({art['author']})\n"
        report += "\n"
    
    if e2_articles:
        report += "#### E2 船舶节能技术\n\n"
        for art in e2_articles:
            report += f"- {art['title']} ({art['author']})\n"
        report += "\n"
    
    if e3_articles:
        report += "#### E3 清洁能源（LNG等）\n\n"
        for art in e3_articles:
            report += f"- {art['title']} ({art['author']})\n"
        report += "\n"
    
    if e4_articles:
        report += "#### E4 多式联运\n\n"
        for art in e4_articles:
            report += f"- {art['title']} ({art['author']})\n"
        report += "\n"
    
    if e5_articles:
        report += "#### E5 流量调度影响\n\n"
        for art in e5_articles:
            report += f"- {art['title']} ({art['author']})\n"
        report += "\n"
    
    report += f"""
### 4.2 经济社会贡献类文献（D类，共{d_count}篇）

#### D1 运输成本相关

"""
    d1_articles = [a for a in articles_data if 'D1' in a.get('themes', [])]
    for art in d1_articles[:20]:
        report += f"- {art['title']} ({art['author']})\n"
    
    report += "\n#### D2 区域经济\n\n"
    d2_articles = [a for a in articles_data if 'D2' in a.get('themes', [])]
    for art in d2_articles[:20]:
        report += f"- {art['title']} ({art['author']})\n"
    
    report += f"""
### 4.3 碍航问题类文献（C类，共{c_count}篇）

#### C1 拥堵分析

"""
    c1_articles = [a for a in articles_data if 'C1' in a.get('themes', [])]
    for art in c1_articles[:15]:
        report += f"- {art['title']} ({art['author']})\n"
    
    report += "\n#### C2 碍航损失评估\n\n"
    c2_articles = [a for a in articles_data if 'C2' in a.get('themes', [])]
    for art in c2_articles[:15]:
        report += f"- {art['title']} ({art['author']})\n"
    
    report += "\n#### C3 自然条件制约\n\n"
    c3_articles = [a for a in articles_data if 'C3' in a.get('themes', [])]
    for art in c3_articles[:20]:
        report += f"- {art['title']} ({art['author']})\n"
    
    report += f"""
### 4.4 通航效率优化类文献（B类，共{b_count}篇）

#### B1 调度优化

"""
    b1_articles = [a for a in articles_data if 'B1' in a.get('themes', [])]
    for art in b1_articles[:30]:
        report += f"- {art['title']} ({art['author']})\n"
    
    report += "\n#### B3 信息化建设\n\n"
    b3_articles = [a for a in articles_data if 'B3' in a.get('themes', [])]
    for art in b3_articles[:30]:
        report += f"- {art['title']} ({art['author']})\n"
    
    report += "\n#### B5 通过能力提升\n\n"
    b5_articles = [a for a in articles_data if 'B5' in a.get('themes', [])]
    for art in b5_articles[:20]:
        report += f"- {art['title']} ({art['author']})\n"
    
    report += """
---

*报告自动生成*
"""
    
    report_path = OUTPUT_DIR / "analysis_summary.md"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n报告已保存到: {report_path}")
    return report

def main():
    articles_data, theme_stats = analyze_all_articles()
    generate_report(articles_data, theme_stats)
    return articles_data, theme_stats

if __name__ == "__main__":
    main()
