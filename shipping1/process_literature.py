#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
处理CNKI文献数据：合并去重、清洗、生成Markdown
"""

import os
import re
from pathlib import Path

# 定义文献来源目录
BASE_DIR = Path(r"C:\Users\DELL\Desktop\shipping")
OUTPUT_DIR = BASE_DIR / "literature_output"

# 定义需要读取的文件
FILES = [
    BASE_DIR / "主题航运+主题三峡" / "CNKI-20260226155153299.txt",
    BASE_DIR / "主题航运+主题三峡" / "CNKI-20260226155347756.txt",
    BASE_DIR / "主题船闸+主题三峡" / "CNKI-20260226155727617.txt",
    BASE_DIR / "主题船闸+主题三峡" / "CNKI-20260226155853849.txt",
    BASE_DIR / "主题船闸+主题三峡" / "CNKI-20260226155954366.txt",
    BASE_DIR / "主题船闸+主题三峡" / "CNKI-20260226160058302.txt",
    BASE_DIR / "主题船闸+主题三峡" / "CNKI-20260226160205791.txt",
    BASE_DIR / "主题船闸+主题三峡" / "CNKI-20260226160255659.txt",
    BASE_DIR / "主题通航+主题三峡" / "CNKI-20260226160442754.txt",
    BASE_DIR / "主题通航+主题三峡" / "CNKI-20260226160540854.txt",
    BASE_DIR / "主题通航+主题三峡" / "CNKI-20260226161013637.txt",
    BASE_DIR / "主题通航+主题三峡" / "CNKI-20260226161038004.txt",
    BASE_DIR / "主题通航能力+主题三峡" / "CNKI-20260226161131226.txt",
]

def parse_cnki_file(filepath):
    """解析CNKI文件，返回文献列表"""
    articles = []
    current_article = {}
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 按SrcDatabase分割文献
    entries = content.split('SrcDatabase-来源库:')
    
    for entry in entries:
        if not entry.strip():
            continue
            
        entry = 'SrcDatabase-来源库:' + entry
        article = {}
        
        # 解析各个字段
        # 来源库
        src_match = re.search(r'SrcDatabase-来源库:\s*(.+)', entry)
        if src_match:
            article['src_database'] = src_match.group(1).strip()
        
        # 题名
        title_match = re.search(r'Title-题名:\s*(.+)', entry)
        if title_match:
            article['title'] = title_match.group(1).strip()
        
        # 作者
        author_match = re.search(r'Author-作者:\s*(.+)', entry)
        if author_match:
            article['author'] = author_match.group(1).strip()
        
        # 单位
        organ_match = re.search(r'Organ-单位:\s*(.+)', entry)
        if organ_match:
            article['organ'] = organ_match.group(1).strip()
        
        # 文献来源
        source_match = re.search(r'Source-文献来源:\s*(.+)', entry)
        if source_match:
            article['source'] = source_match.group(1).strip()
        
        # 关键词
        keyword_match = re.search(r'Keyword-关键词:\s*(.+)', entry)
        if keyword_match:
            article['keyword'] = keyword_match.group(1).strip()
        
        # 摘要
        summary_match = re.search(r'Summary-摘要:\s*(.+)', entry)
        if summary_match:
            article['summary'] = summary_match.group(1).strip()
        
        # 发表时间
        pubtime_match = re.search(r'PubTime-发表时间:\s*(.+)', entry)
        if pubtime_match:
            article['pub_time'] = pubtime_match.group(1).strip()
        
        if article.get('title'):
            articles.append(article)
    
    return articles

def main():
    print("=" * 60)
    print("开始处理CNKI文献数据")
    print("=" * 60)
    
    # 第一步：读取所有文献
    all_articles = []
    for filepath in FILES:
        print(f"读取: {filepath.name}")
        articles = parse_cnki_file(filepath)
        print(f"  - 获取 {len(articles)} 条文献")
        all_articles.extend(articles)
    
    print(f"\n原始文献总数: {len(all_articles)}")
    
    # 第二步：去重（按题名）
    seen_titles = set()
    unique_articles = []
    for article in all_articles:
        title = article.get('title', '')
        if title and title not in seen_titles:
            seen_titles.add(title)
            unique_articles.append(article)
    
    print(f"去重后文献数: {len(unique_articles)}")
    
    # 第三步：清洗数据 - 删除缺失作者、摘要、关键词的文献
    valid_articles = []
    removed_reasons = {'no_author': 0, 'no_summary': 0, 'no_keyword': 0}
    
    for article in unique_articles:
        # 检查缺失字段
        if not article.get('author'):
            removed_reasons['no_author'] += 1
            continue
        if not article.get('summary'):
            removed_reasons['no_summary'] += 1
            continue
        if not article.get('keyword'):
            removed_reasons['no_keyword'] += 1
            continue
        
        # 检查摘要是否有效（不是政策通告类）
        summary = article.get('summary', '')
        if summary.startswith('<正>') and len(summary) < 200:
            # 短小的政策摘要，不保留
            continue
            
        valid_articles.append(article)
    
    print(f"\n清洗后有效文献数: {len(valid_articles)}")
    print(f"  - 删除无作者: {removed_reasons['no_author']}")
    print(f"  - 删除无摘要: {removed_reasons['no_summary']}")
    print(f"  - 删除无关键词: {removed_reasons['no_keyword']}")
    
    # 第四步：生成Markdown文件
    print(f"\n生成Markdown文件到: {OUTPUT_DIR}")
    for i, article in enumerate(valid_articles, 1):
        # 生成文件名（移除非法字符）
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', article.get('title', f'article_{i}'))
        safe_title = safe_title[:80]  # 限制长度
        
        md_filename = f"{i:03d}_{safe_title}.md"
        md_filepath = OUTPUT_DIR / md_filename
        
        # 生成Markdown内容
        md_content = f"""# {article.get('title', '无题名')}

## 基本信息

- **作者**: {article.get('author', '未知')}
- **单位**: {article.get('organ', '未知')}
- **来源**: {article.get('source', '未知')}
- **发表时间**: {article.get('pub_time', '未知')}
- **文献类型**: {article.get('src_database', '未知')}

## 关键词

{article.get('keyword', '无')}

## 摘要

{article.get('summary', '无')}

---
*文献编号: {i:03d}*
"""
        
        with open(md_filepath, 'w', encoding='utf-8') as f:
            f.write(md_content)
    
    print(f"已生成 {len(valid_articles)} 个Markdown文件")
    
    # 保存文献索引信息
    index_content = "# 文献索引\n\n"
    for i, article in enumerate(valid_articles, 1):
        index_content += f"{i:03d}. {article.get('title', '无题名')} - {article.get('author', '未知')}\n"
    
    with open(OUTPUT_DIR / "文献索引.md", 'w', encoding='utf-8') as f:
        f.write(index_content)
    
    print("\n处理完成!")
    print(f"有效文献总数: {len(valid_articles)}")
    
    return valid_articles

if __name__ == "__main__":
    main()
