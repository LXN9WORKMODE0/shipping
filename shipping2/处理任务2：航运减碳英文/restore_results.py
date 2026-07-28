#!/usr/bin/env python3
import json, re
from pathlib import Path
from datetime import datetime

STATE_FILE = Path("process_state.json")
OUTPUT_DIR = Path("output")
SCORE_THRESHOLD = 60

def parse_md(fp):
    with open(fp, 'r', encoding='utf-8') as f: c = f.read()
    info = {}
    m = re.search(r'^#\s+(.+)$', c, re.MULTILINE)
    if m: info['标题'] = m.group(1).strip()
    m = re.search(r'\*\*作者[：:]\s*(.+?)(?:\n|$)', c)
    if m: info['作者'] = m.group(1).strip()
    m = re.search(r'\*\*年份[：:]\s*(\d{4})', c)
    if m: info['年份'] = m.group(1).strip()
    m = re.search(r'\*\*期刊/会议[：:]\s*(.+?)(?:\n|$)', c)
    if m: info['期刊/会议'] = m.group(1).strip()
    m = re.search(r'\*\*文献类型[：:]\s*(.+?)(?:\n|$)', c)
    if m: info['文献类型'] = m.group(1).strip()
    m = re.search(r'\*\*DOI[：:]\s*(.+?)(?:\n|$)', c)
    if m: info['DOI'] = m.group(1).strip()
    return info

with open(STATE_FILE, 'r', encoding='utf-8') as f:
    state = json.load(f)
print(f'状态记录: {len(state)}')

records = {k:v for k,v in state.items() if k != 'index.md'}
paper_map = {fp.name: parse_md(fp) for fp in OUTPUT_DIR.glob("*.md") if fp.name != "index.md" and parse_md(fp).get('标题')}

d1, d2, d3 = [], [], []
for fn, st in records.items():
    sc = st.get('scores', {})
    s1, s2, s3 = sc.get('d1',0), sc.get('d2',0), sc.get('d3',0)
    info = paper_map.get(fn, {})
    if not info.get('标题'): info['标题'] = st.get('title', fn)
    if s1 >= SCORE_THRESHOLD: d1.append((fn, info, s1))
    if s2 >= SCORE_THRESHOLD: d2.append((fn, info, s2))
    if s3 >= SCORE_THRESHOLD: d3.append((fn, info, s3))

print(f'方向1:{len(d1)} 方向2:{len(d2)} 方向3:{len(d3)}')

def save(results, title, out_file):
    results.sort(key=lambda x: x[2], reverse=True)
    with open(out_file, 'w', encoding='utf-8') as f:
        f.write(f"# {title}（质评版）\n\n")
        f.write(f"筛选时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"## 筛选标准\n- 入门分数: ≥{SCORE_THRESHOLD}分\n\n---\n\n")
        f.write(f"共{len(results)}篇\n\n---\n\n")
        for fn, info, score in results:
            f.write(f"## {fn}\n\n**标题**: {info.get('标题','')}\n\n")
            if info.get('作者'): f.write(f"**作者**: {info.get('作者')}\n\n")
            if info.get('年份'): f.write(f"**年份**: {info.get('年份')}\n\n")
            if info.get('期刊/会议'): f.write(f"**期刊/会议**: {info.get('期刊/会议')}\n\n")
            if info.get('文献类型'): f.write(f"**文献类型**: {info.get('文献类型')}\n\n")
            if info.get('DOI'): f.write(f"**DOI**: {info.get('DOI')}\n\n")
            f.write(f"**质量评分**: {score}分\n\n")
            f.write("**相关性说明**: (从状态文件恢复)\n\n---\n\n")
    print(f"已保存: {out_file}")

save(d1, "三峡枢纽航运经济社会效益相关文献汇总", "三峡枢纽航运经济社会效益相关文献汇总_质评版.md")
save(d2, "三峡枢纽航运节能减排相关文献汇总", "三峡枢纽航运节能减排相关文献汇总_质评版.md")
save(d3, "三峡枢纽水运通过能力相关文献汇总", "三峡枢纽水运通过能力相关文献汇总_质评版.md")
print("恢复完成!")
