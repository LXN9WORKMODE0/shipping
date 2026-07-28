#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将CNKI导出的题录TXT文件转换为Markdown文件
"""

import os
import re
from pathlib import Path

# 配置
INPUT_FILE = "导出题录.txt"
OUTPUT_DIR = "output"

def read_txt_file(filepath):
    """读取TXT文件内容"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()

def parse_citations(text):
    """解析TXT文件，提取每条题录"""
    # 按空行分割每条题录
    blocks = text.split('\n\n')
    
    citations = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        
        citation = parse_citation_block(block)
        if citation:
            citations.append(citation)
    
    return citations

def parse_citation_block(block):
    """解析单条题录"""
    fields = {}
    lines = block.split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # 匹配字段格式: {FieldName}: value
        match = re.match(r'^\{([^}]+)\}:\s*(.*)$', line)
        if match:
            field_name = match.group(1).strip()
            field_value = match.group(2).strip()
            fields[field_name] = field_value
    
    return fields if fields else None

def is_complete_citation(citation):
    """检查题录是否信息完整"""
    required_fields = ['Author', 'Year', 'Title', 'Journal', 'Abstract']
    
    for field in required_fields:
        if field not in citation:
            return False
        
        value = citation[field].strip()
        if not value or value.lower() == 'null':
            return False
    
    return True

def generate_md_content(citation):
    """生成Markdown格式内容"""
    md_lines = []
    
    # 标题
    title = citation.get('Title', '').strip()
    md_lines.append(f"# {title}")
    md_lines.append("")
    
    # 作者
    author = citation.get('Author', '').strip()
    if author:
        md_lines.append(f"**作者**: {author}")
    
    # 年份
    year = citation.get('Year', '').strip()
    if year:
        md_lines.append(f"**年份**: {year}")
    
    # 文献类型
    ref_type = citation.get('Reference Type', '').strip()
    if ref_type:
        md_lines.append(f"**文献类型**: {ref_type}")
    
    # 期刊/会议
    journal = citation.get('Journal', '').strip()
    if journal:
        md_lines.append(f"**期刊/会议**: {journal}")
    
    # 卷期页码
    volume = citation.get('Volume', '').strip()
    issue = citation.get('Issue', '').strip()
    pages = citation.get('Pages', '').strip()
    
    volume_info = []
    if volume and volume.lower() != 'null':
        volume_info.append(f"卷: {volume}")
    if issue and issue.lower() != 'null':
        volume_info.append(f"期: {issue}")
    if pages and pages.lower() != 'null':
        volume_info.append(f"页码: {pages}")
    
    if volume_info:
        md_lines.append(f"**卷期页码**: {', '.join(volume_info)}")
    
    # 日期
    date = citation.get('Date', '').strip()
    if date:
        md_lines.append(f"**日期**: {date}")
    
    # 关键词
    keywords = citation.get('Keywords', '').strip()
    if keywords and keywords.lower() != 'null':
        md_lines.append(f"**关键词**: {keywords}")
    
    # 摘要
    abstract = citation.get('Abstract', '').strip()
    if abstract:
        md_lines.append("")
        md_lines.append("## 摘要")
        md_lines.append(abstract)
    
    # DOI
    doi = citation.get('DOI', '').strip()
    if doi and doi.lower() != 'null':
        md_lines.append("")
        md_lines.append(f"**DOI**: {doi}")
    
    # URL
    url = citation.get('URL', '').strip()
    if url and url.lower() != 'null':
        md_lines.append("")
        md_lines.append(f"**URL**: {url}")
    
    return '\n'.join(md_lines)

def sanitize_filename(title):
    """将标题转换为安全的文件名"""
    # 移除非ASCII字符，用拼音或编号代替
    # 这里使用标题的前几个字符 + 编号
    # 先去除特殊字符
    safe_title = re.sub(r'[^\w\s\u4e00-\u9fff]', '', title)
    safe_title = safe_title.strip()
    
    # 取前20个字符（如果不足则全部）
    if len(safe_title) > 20:
        safe_title = safe_title[:20]
    
    return safe_title

def main():
    """主函数"""
    print("=" * 50)
    print("开始处理题录文件...")
    print("=" * 50)
    
    # 确保输出目录存在
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 读取文件
    print(f"\n1. 读取文件: {INPUT_FILE}")
    text = read_txt_file(INPUT_FILE)
    print(f"   文件读取完成，总字符数: {len(text)}")
    
    # 解析题录
    print("\n2. 解析题录...")
    all_citations = parse_citations(text)
    print(f"   共解析到 {len(all_citations)} 条题录")
    
    # 筛选完整题录
    print("\n3. 筛选完整题录...")
    complete_citations = [c for c in all_citations if is_complete_citation(c)]
    incomplete_count = len(all_citations) - len(complete_citations)
    print(f"   完整题录: {len(complete_citations)} 条")
    print(f"   不完整题录（已剔除）: {incomplete_count} 条")
    
    # 生成Markdown文件
    print(f"\n4. 生成Markdown文件到 {OUTPUT_DIR} 目录...")
    
    # 生成汇总索引
    index_lines = ["# 题录索引\n"]
    
    for i, citation in enumerate(complete_citations, 1):
        # 生成MD内容
        md_content = generate_md_content(citation)
        
        # 生成文件名
        title = citation.get('Title', '').strip()
        safe_title = sanitize_filename(title)
        filename = f"{i:04d}_{safe_title}.md"
        
        # 写入文件
        filepath = os.path.join(OUTPUT_DIR, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        # 添加到索引
        index_lines.append(f"{i}. [{title}]({filename})\n")
        
        if i % 500 == 0:
            print(f"   已处理 {i}/{len(complete_citations)} 条...")
    
    print(f"   完成! 共生成 {len(complete_citations)} 个MD文件")
    
    # 写入索引文件
    index_path = os.path.join(OUTPUT_DIR, "index.md")
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(''.join(index_lines))
    print(f"\n5. 索引文件已生成: index.md")
    
    print("\n" + "=" * 50)
    print("处理完成!")
    print("=" * 50)

if __name__ == "__main__":
    main()
