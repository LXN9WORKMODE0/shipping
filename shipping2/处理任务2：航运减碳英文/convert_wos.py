#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将Web of Science导出的TXT文件转换为Markdown文件
"""

import os
import re
import sys

# 设置UTF-8输出
sys.stdout.reconfigure(encoding='utf-8')

# 配置
INPUT_FILES = [
    "savedrecs.txt",
    "savedrecs (2).txt",
    "savedrecs (3).txt"
]
OUTPUT_DIR = "output"

def read_txt_file(filepath):
    """读取TXT文件内容"""
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()

def parse_wos_citations(text):
    """解析WOS格式文本，提取每条题录"""
    # 按 ER 分割
    records = text.split('\nER\n')

    citations = []
    for record in records:
        if not record.strip() or record.strip() in ['ER', '\nER']:
            continue

        citation = parse_single_wos_record(record)
        if citation:
            citations.append(citation)

    return citations

def parse_single_wos_record(record_text):
    """解析单条WOS记录"""
    fields = {}
    lines = record_text.strip().split('\n')

    current_tag = None
    current_value = ""

    for line in lines:
        # 检查是否是新的标签行 (两个大写字母后紧跟空格)
        if len(line) >= 3 and line[:2].isupper() and line[2] == ' ':
            # 保存之前的字段
            if current_tag:
                fields[current_tag] = current_value.strip()

            current_tag = line[:2]
            current_value = line[3:]
        elif line.startswith('   ') and current_tag:
            # continuation line - 缩进的行
            current_value += ' ' + line.strip()

    # 保存最后一个字段
    if current_tag:
        fields[current_tag] = current_value.strip()

    return fields if fields else None

def map_wos_to_standard(wos_fields):
    """将WOS字段映射到标准格式"""
    # 字段映射
    field_map = {
        'TI': 'Title',
        'SO': 'Journal',
        'PY': 'Year',
        'AB': 'Abstract',
        'DI': 'DOI',
        'VL': 'Volume',
        'AR': 'Article Number',
        'BP': 'Begin Page',
        'EP': 'End Page',
        'DT': 'Document Type',
        'PD': 'Publication Date',
        'EA': 'Early Access',
        'TC': 'Times Cited',
    }

    citation = {}
    for wos_tag, std_name in field_map.items():
        if wos_tag in wos_fields:
            citation[std_name] = wos_fields[wos_tag]

    # 处理作者 - WOS可能有多个AU行
    if 'AU' in wos_fields:
        citation['Author'] = wos_fields['AU']
    else:
        citation['Author'] = ''

    return citation

def is_complete_citation(citation):
    """检查题录是否信息完整"""
    required_fields = ['Author', 'Year', 'Title', 'Journal']

    for field in required_fields:
        if field not in citation:
            return False
        value = citation[field].strip()
        if not value:
            return False

    return True

def generate_md_content(citation, idx):
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
    doc_type = citation.get('Document Type', '').strip()
    if doc_type:
        md_lines.append(f"**文献类型**: {doc_type}")

    # 期刊
    journal = citation.get('Journal', '').strip()
    if journal:
        md_lines.append(f"**期刊/会议**: {journal}")

    # 卷期页
    volume = citation.get('Volume', '').strip()
    article_num = citation.get('Article Number', '').strip()
    bp = citation.get('Begin Page', '').strip()
    ep = citation.get('End Page', '').strip()

    vol_info = []
    if volume:
        vol_info.append(f"卷: {volume}")
    if article_num:
        vol_info.append(f"文章号: {article_num}")
    if bp:
        if ep:
            vol_info.append(f"页: {bp}-{ep}")
        else:
            vol_info.append(f"页: {bp}")

    if vol_info:
        md_lines.append(f"**卷期页**: {', '.join(vol_info)}")

    # 出版日期
    pub_date = citation.get('Publication Date', '').strip()
    if pub_date:
        md_lines.append(f"**出版日期**: {pub_date}")

    # 引用次数
    tc = citation.get('Times Cited', '').strip()
    if tc:
        md_lines.append(f"**引用次数**: {tc}")

    # DOI
    doi = citation.get('DOI', '').strip()
    if doi:
        md_lines.append("")
        md_lines.append(f"**DOI**: {doi}")

    # 摘要
    abstract = citation.get('Abstract', '').strip()
    if abstract:
        md_lines.append("")
        md_lines.append("## 摘要")
        md_lines.append(abstract)

    return '\n'.join(md_lines)

def sanitize_filename(title):
    """安全的文件名"""
    safe = re.sub(r'[^\w\s\u4e00-\u9fff]', '', title)
    safe = safe.strip()
    if len(safe) > 20:
        safe = safe[:20]
    return safe

def main():
    """主函数"""
    print("=" * 60)
    print("开始处理Web of Science题录文件...")
    print("=" * 60)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_citations = []

    for input_file in INPUT_FILES:
        if not os.path.exists(input_file):
            print(f"跳过: {input_file}")
            continue

        print(f"\n处理: {input_file}")
        text = read_txt_file(input_file)
        citations = parse_wos_citations(text)

        # 转换格式
        mapped = [map_wos_to_standard(c) for c in citations]
        all_citations.extend(mapped)
        print(f"  解析 {len(citations)} 条")

    print(f"\n总计: {len(all_citations)} 条")

    # 筛选完整记录
    complete = [c for c in all_citations if is_complete_citation(c)]
    print(f"完整记录: {len(complete)} 条")

    # 生成MD文件
    print(f"\n生成Markdown文件...")

    index_lines = ["# 题录索引\n"]

    for i, cit in enumerate(complete, 1):
        md = generate_md_content(cit, i)
        title = cit.get('Title', '').strip()
        filename = f"{i:04d}_{sanitize_filename(title)}.md"

        with open(f"{OUTPUT_DIR}/{filename}", 'w', encoding='utf-8') as f:
            f.write(md)

        index_lines.append(f"{i}. [{title}]({filename})\n")

        if i % 200 == 0:
            print(f"  已处理 {i}/{len(complete)}")

    # 索引文件
    with open(f"{OUTPUT_DIR}/index.md", 'w', encoding='utf-8') as f:
        f.write(''.join(index_lines))

    print(f"\n完成! 共生成 {len(complete)} 个MD文件")
    print(f"索引: {OUTPUT_DIR}/index.md")

if __name__ == "__main__":
    main()