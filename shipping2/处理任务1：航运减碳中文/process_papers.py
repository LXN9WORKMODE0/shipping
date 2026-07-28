"""
三峡枢纽文献自动化处理脚本
使用硅基流动 API (DeepSeek V3.2) 逐个处理 MD 文件，避免上下文积累
"""

import os
import json
import re
import time
import requests
import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============== 从配置文件加载 ==============
CONFIG_FILE = Path(__file__).parent / "config.json"

def load_config():
    """从 config.json 加载配置"""
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

config = load_config()

API_KEY = config["api_key"]
MODEL = config["model"]
API_URL = config["api_url"]
OUTPUT_DIR = Path(config["input_dir"])
RESULT_FILE_1 = Path(config["output_dir"]) / "三峡枢纽航运经济社会效益相关文献汇总_质评版.md"
RESULT_FILE_2 = Path(config["output_dir"]) / "三峡枢纽航运节能减排相关文献汇总_质评版.md"
RESULT_FILE_3 = Path(config["output_dir"]) / "三峡枢纽水运通过能力相关文献汇总_质评版.md"
STATE_FILE = Path(config["state_file"])
FAILED_FILE = Path(config["output_dir"]) / "failed_api_calls.json"  # 失败记录文件
SCORE_THRESHOLD = config.get("score_threshold", 60)
MAX_WORKERS = config.get("max_workers", 5)  # 并发工作线程数
MAX_RETRIES = config.get("max_retries", 2)  # API 重试次数
RETRY_DELAY = config.get("retry_delay", 10)  # API 重试延迟（秒）

# 线程锁
state_lock = threading.Lock()
results_lock = threading.Lock()

# ============== 评分标准 ==============
def calculate_score(paper_info):
    """根据论文信息计算质量评分"""
    total_score = 0

    # 1. 内容相关性 - 由 LLM 判断
    relevance_score = 0

    # 2. 年份评分 (25%)
    try:
        year = int(paper_info.get('年份', 0))
        if 2016 <= year <= 2025:
            year_score = 25
        elif 2011 <= year <= 2015:
            year_score = 15
        elif 2006 <= year <= 2010:
            year_score = 10
        else:
            year_score = 5
    except:
        year_score = 5

    # 3. 文献类型评分 (20%)
    doc_type = paper_info.get('文献类型', '')
    if 'thesis' in doc_type.lower() or '硕士' in doc_type or '博士' in doc_type:
        doc_type_score = 20
    elif 'journal' in doc_type.lower() and '核心' in doc_type:
        doc_type_score = 20
    elif 'journal' in doc_type.lower():
        doc_type_score = 15
    elif 'conference' in doc_type.lower():
        doc_type_score = 10
    else:
        doc_type_score = 10

    # 4. 摘要质量评分 (15%)
    abstract = paper_info.get('摘要', '')
    if len(abstract) > 200:
        abstract_score = 15
    elif len(abstract) > 50:
        abstract_score = 10
    else:
        abstract_score = 5

    # 内容相关性单独返回，等待 LLM 评估
    return {
        'year_score': year_score,
        'doc_type_score': doc_type_score,
        'abstract_score': abstract_score,
        'relevance_score': 0  # 由 LLM 评估
    }

# ============== API 调用 ==============

def call_api(paper_info, direction):
    """调用硅基流动 API 分析论文，包含重试机制"""

    prompt = f"""你是一位学术文献评估专家。请分析以下论文，判断是否与指定研究方向相关，并给出质量评分。

## 论文信息：
标题：{paper_info.get('标题', '')}
作者：{paper_info.get('作者', '')}
年份：{paper_info.get('年份', '')}
文献类型：{paper_info.get('文献类型', '')}
期刊/会议：{paper_info.get('期刊/会议', '')}
摘要：{paper_info.get('摘要', '')}
关键词：{paper_info.get('关键词', '')}

## 研究方向：
{direction}

## 评估要求：
1. 判断论文是否与该研究方向相关（完全相关=40分，部分相关=20分，不相关=0分）
2. 判断文献类型价值（核心期刊/学位论文=20分，普通期刊=15分，会议/简报=10分）
3. 判断摘要质量（完整有方法数据=15分，简短=10分，无/极简=5分）

## 输出格式（严格按此格式输出）：
【评估结果】
相关得分: XX
类型得分: XX
摘要得分: XX
相关性说明: XXX
是否推荐: 是/否"""

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "max_tokens": 500
    }

    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(API_URL, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            result = response.json()
            content = result['choices'][0]['message']['content']

            # 检查返回内容是否有效（不是NA或空）
            if content and content.strip() and 'N/A' not in content[:50]:
                return content

            # 如果返回内容看起来无效，视为失败
            if attempt < MAX_RETRIES - 1:
                print(f"  ⚠ API 返回内容无效，{RETRY_DELAY}秒后重试 ({attempt + 1}/{MAX_RETRIES})...")
                time.sleep(RETRY_DELAY)
            else:
                return content  # 返回最后一次结果，让解析函数处理

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                print(f"  ⚠ API 调用失败: {e}，{RETRY_DELAY}秒后重试 ({attempt + 1}/{MAX_RETRIES})...")
                time.sleep(RETRY_DELAY)
            else:
                print(f"  ✗ API 调用失败: {e}")
                return None

    return None

def parse_api_response(response_text):
    """解析 API 返回的评估结果"""
    if not response_text:
        return None

    result = {
        'relevance_score': 0,
        'doc_type_score': 10,
        'abstract_score': 10,
        'is_recommended': False,
        'explanation': ''
    }

    # 提取相关得分
    match = re.search(r'相关得分[：:]\s*(\d+)', response_text)
    if match:
        result['relevance_score'] = int(match.group(1))

    # 提取类型得分
    match = re.search(r'类型得分[：:]\s*(\d+)', response_text)
    if match:
        result['doc_type_score'] = int(match.group(1))

    # 提取摘要得分
    match = re.search(r'摘要得分[：:]\s*(\d+)', response_text)
    if match:
        result['abstract_score'] = int(match.group(1))

    # 提取相关性说明
    match = re.search(r'相关性说明[：:]\s*(.+)', response_text)
    if match:
        result['explanation'] = match.group(1).strip()

    # 提取是否推荐
    if '是' in response_text and '否' not in response_text.split('是否推荐')[1][:10] if '是否推荐' in response_text else False:
        result['is_recommended'] = True

    # 检查是否推荐
    if '是否推荐' in response_text:
        for line in response_text.split('\n'):
            if '是否推荐' in line:
                if '是' in line and '否' not in line:
                    result['is_recommended'] = True
                break

    result['total_score'] = result['relevance_score'] + result['doc_type_score'] + result['abstract_score']

    return result

# ============== 文件处理 ==============
def parse_md_file(file_path):
    """解析 MD 文件，提取论文信息"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    paper_info = {}

    # 提取标题（第一行 # 后的内容）
    match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    if match:
        paper_info['标题'] = match.group(1).strip()

    # 提取作者
    match = re.search(r'\*\*作者[：:]\s*(.+?)(?:\n|$)', content)
    if match:
        paper_info['作者'] = match.group(1).strip()

    # 提取年份
    match = re.search(r'\*\*年份[：:]\s*(\d{4})', content)
    if match:
        paper_info['年份'] = match.group(1).strip()

    # 提取文献类型
    match = re.search(r'\*\*文献类型[：:]\s*(.+?)(?:\n|$)', content)
    if match:
        paper_info['文献类型'] = match.group(1).strip()

    # 提取期刊/会议
    match = re.search(r'\*\*期刊/会议[：:]\s*(.+?)(?:\n|$)', content)
    if match:
        paper_info['期刊/会议'] = match.group(1).strip()

    # 提取摘要
    match = re.search(r'##\s*摘要\s*\n(.+?)(?:\n\*\*|\Z)', content, re.DOTALL)
    if match:
        paper_info['摘要'] = match.group(1).strip()

    # 提取关键词
    match = re.search(r'\*\*关键词[：:]\s*(.+?)(?:\n|$)', content)
    if match:
        paper_info['关键词'] = match.group(1).strip()

    return paper_info

def get_file_number(file_path):
    """从文件名提取数字编号"""
    basename = os.path.basename(file_path)
    match = re.match(r'^(\d+)', basename)
    if match:
        return int(match.group(1))
    return 0

def process_single_file(file_path, direction_1, direction_2, direction_3):
    """处理单个文件（供并发调用）"""
    file_name = file_path.name
    result_item = {
        'file': file_name,
        'file_path': str(file_path),
        'success': False,      # 文件解析是否成功
        'api_failed': False,   # API调用是否失败
        'eval_1': None,
        'eval_2': None,
        'eval_3': None,
        'paper_info': None
    }

    try:
        # 解析文件
        paper_info = parse_md_file(file_path)
        if not paper_info.get('标题'):
            print(f"  警告: 无法解析文件 {file_name}")
            result_item['success'] = True  # 标记为已处理（虽然解析失败）
            result_item['api_failed'] = True  # 也视为失败，需要重试
            return result_item

        result_item['paper_info'] = paper_info

        # 并发调用 API 评估三个方向
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_1 = executor.submit(call_api, paper_info, direction_1)
            future_2 = executor.submit(call_api, paper_info, direction_2)
            future_3 = executor.submit(call_api, paper_info, direction_3)

            response_1 = future_1.result()
            time.sleep(0.1)
            response_2 = future_2.result()
            time.sleep(0.1)
            response_3 = future_3.result()

        # 解析结果
        eval_1 = parse_api_response(response_1)
        eval_2 = parse_api_response(response_2)
        eval_3 = parse_api_response(response_3)

        result_item['eval_1'] = eval_1
        result_item['eval_2'] = eval_2
        result_item['eval_3'] = eval_3
        result_item['success'] = True

        # 检查是否有任何API调用失败
        if eval_1 is None or eval_2 is None or eval_3 is None:
            result_item['api_failed'] = True
            print(f"  ⚠ {file_name}: API调用失败，需要重试")
        else:
            print(f"  ✓ {file_name}: D1={eval_1.get('total_score', 0)}, D2={eval_2.get('total_score', 0)}, D3={eval_3.get('total_score', 0)}")

    except Exception as e:
        print(f"  ✗ 处理失败 {file_name}: {e}")
        result_item['api_failed'] = True

    return result_item

# ============== 主处理逻辑 ==============
def process_papers():
    """主处理函数"""

    # 加载已处理状态（成功处理的文档，包含标题和分数）
    # 格式: {filename: {"title": xxx, "scores": {d1: xx, d2: xx, d3: xx}}}
    success_records = {}
    if STATE_FILE.exists():
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                # 兼容旧格式
                success_records = {item: {"title": "", "scores": {}} for item in data}
            else:
                success_records = data

    # 加载失败记录
    # 格式: {filename: {"title": xxx, "error": xxx}}
    failed_records = {}
    if FAILED_FILE.exists():
        with open(FAILED_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                # 兼容旧格式
                failed_records = {item: {"title": "", "error": ""} for item in data}
            else:
                failed_records = data

    # 获取所有 MD 文件
    md_files = list(OUTPUT_DIR.glob("*.md"))
    md_files.sort(key=lambda x: get_file_number(x))

    # 过滤待处理文件（包括失败记录中的文件）
    pending_files = [f for f in md_files if f.name not in success_records]

    # 如果有失败记录要重试，添加它们
    retry_files = [f for f in md_files if f.name in failed_records and f.name not in success_records]
    if retry_files:
        print(f"发现 {len(retry_files)} 个文件需要重试")
        pending_files.extend(retry_files)

    # 去重并保持排序
    seen = set()
    unique_pending = []
    for f in pending_files:
        if f.name not in seen:
            seen.add(f.name)
            unique_pending.append(f)
    pending_files = sorted(unique_pending, key=lambda x: get_file_number(x))

    print(f"总文件数: {len(md_files)}")
    print(f"已处理: {len(success_records)}")
    print(f"失败待重试: {len(failed_records)}")
    print(f"待处理: {len(pending_files)}")
    print(f"并发数: {MAX_WORKERS}")

    # 存储结果
    results_d1 = []  # 方向1：经济社会效益
    results_d2 = []  # 方向2：节能减排
    results_d3 = []  # 方向3：水运通过能力

    # 研究方向描述（从配置文件读取）
    directions_config = config.get("directions", {})
    direction_1 = directions_config.get("direction_1", {}).get("description", "")
    direction_2 = directions_config.get("direction_2", {}).get("description", "")
    direction_3 = directions_config.get("direction_3", {}).get("description", "")

    if not pending_files:
        print("没有待处理的文件")
        if failed_records:
            print(f"但有 {len(failed_records)} 个失败文件需要重试")
        # 仍然需要加载已有的结果
        load_existing_results(results_d1, results_d2, results_d3, success_records)
        save_results(results_d1, results_d2, results_d3)
        return

    # 使用线程池并发处理
    print(f"\n开始并发处理...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # 提交所有任务
        future_to_file = {
            executor.submit(process_single_file, file_path, direction_1, direction_2, direction_3): file_path
            for file_path in pending_files
        }

        # 收集结果
        for future in as_completed(future_to_file):
            file_path = future_to_file[future]
            try:
                result_item = future.result()

                if result_item['success']:
                    # 检查API是否调用成功
                    api_failed = result_item.get('api_failed', False)
                    paper_info = result_item['paper_info']
                    eval_1 = result_item['eval_1']
                    eval_2 = result_item['eval_2']
                    eval_3 = result_item['eval_3']
                    file_name = result_item['file']
                    title = paper_info.get('标题', '') if paper_info else ''

                    if api_failed:
                        # API调用失败，记录到失败列表（包含标题）
                        with state_lock:
                            failed_records[file_name] = {
                                "title": title,
                                "error": "API调用失败",
                                "scores": {
                                    "d1": eval_1.get('total_score', 0) if eval_1 else None,
                                    "d2": eval_2.get('total_score', 0) if eval_2 else None,
                                    "d3": eval_3.get('total_score', 0) if eval_3 else None
                                }
                            }
                            # 从失败列表中移除如果之前在里面
                            if file_name in failed_records:
                                failed_records[file_name] = {
                                    "title": title,
                                    "error": "API调用失败",
                                    "scores": {
                                        "d1": eval_1.get('total_score', 0) if eval_1 else None,
                                        "d2": eval_2.get('total_score', 0) if eval_2 else None,
                                        "d3": eval_3.get('total_score', 0) if eval_3 else None
                                    }
                                }
                            save_failed_files(failed_records)
                    else:
                        # API调用成功，更新成功记录（包含标题和分数）
                        with state_lock:
                            success_records[file_name] = {
                                "title": title,
                                "scores": {
                                    "d1": eval_1.get('total_score', 0) if eval_1 else 0,
                                    "d2": eval_2.get('total_score', 0) if eval_2 else 0,
                                    "d3": eval_3.get('total_score', 0) if eval_3 else 0
                                }
                            }
                            # 从失败列表中移除（如果之前在失败列表中）
                            if file_name in failed_records:
                                del failed_records[file_name]
                                save_failed_files(failed_records)
                            save_state(success_records)

                    # 处理评估结果（即使有部分API失败，成功的仍要处理）
                    # 方向1（只有成功获取结果时才添加）
                    if eval_1 and eval_1.get('is_recommended') and eval_1.get('total_score', 0) >= SCORE_THRESHOLD:
                        with results_lock:
                            results_d1.append({
                                'file': result_item['file'],
                                'info': paper_info,
                                'eval': eval_1
                            })

                    # 方向2
                    if eval_2 and eval_2.get('is_recommended') and eval_2.get('total_score', 0) >= SCORE_THRESHOLD:
                        with results_lock:
                            results_d2.append({
                                'file': result_item['file'],
                                'info': paper_info,
                                'eval': eval_2
                            })

                    # 方向3
                    if eval_3 and eval_3.get('is_recommended') and eval_3.get('total_score', 0) >= SCORE_THRESHOLD:
                        with results_lock:
                            results_d3.append({
                                'file': result_item['file'],
                                'info': paper_info,
                                'eval': eval_3
                            })

            except Exception as e:
                print(f"处理文件 {file_path.name} 时出错: {e}")

    # 加载已有的结果（合并，防止覆盖）
    load_existing_results(results_d1, results_d2, results_d3, success_records)

    # 保存结果
    save_results(results_d1, results_d2, results_d3)

    print("\n处理完成!")
    print(f"方向1 (经济社会效益): {len(results_d1)} 篇")
    print(f"方向2 (节能减排): {len(results_d2)} 篇")
    print(f"方向3 (水运通过能力): {len(results_d3)} 篇")
    print(f"失败待重试: {len(failed_records)} 篇")

def save_state(success_records):
    """保存成功处理的文档状态（包含标题和分数）"""
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(success_records, f, ensure_ascii=False, indent=2)

def save_failed_files(failed_records):
    """保存失败的文件列表（包含标题）"""
    with open(FAILED_FILE, 'w', encoding='utf-8') as f:
        json.dump(failed_records, f, ensure_ascii=False, indent=2)

def load_existing_results(results_d1, results_d2, results_d3, processed_set):
    """加载已有的结果文件，合并重复的推荐结果

    Args:
        results_d1, results_d2, results_d3: 结果列表
        processed_set: 已处理文件的集合（用于去重）
    """
    # 用于去重：已加载的文件名集合
    loaded_files = set()

    # 1. 先收集本次新处理的文件（从results_d1/d2/d3）
    for item in results_d1:
        loaded_files.add(item['file'])
    for item in results_d2:
        loaded_files.add(item['file'])
    for item in results_d3:
        loaded_files.add(item['file'])

    # 2. 收集之前已成功处理的论文（从processed_set）
    # 这确保了第二次运行时，已处理过的论文不会被重复添加到results列表
    for file_name in processed_set:
        loaded_files.add(file_name)

    # 加载方向1结果
    if RESULT_FILE_1.exists():
        with open(RESULT_FILE_1, 'r', encoding='utf-8') as f:
            content = f.read()
        # 解析现有结果，提取文件名和基本信息
        # 格式：## 文件名\n**标题**: xxx...
        current_file = None
        current_info = {}
        current_eval = {}
        for line in content.split('\n'):
            if line.startswith('## '):
                # 保存上一个
                if current_file and current_file not in loaded_files and current_eval.get('total_score', 0) >= SCORE_THRESHOLD:
                    results_d1.append({
                        'file': current_file,
                        'info': current_info,
                        'eval': current_eval
                    })
                # 新开始
                current_file = line[3:].strip()
                current_info = {}
                current_eval = {}
            elif line.startswith('**标题**:'):
                current_info['标题'] = line.replace('**标题**:', '').strip()
            elif line.startswith('**作者**:'):
                current_info['作者'] = line.replace('**作者**:', '').strip()
            elif line.startswith('**年份**:'):
                current_info['年份'] = line.replace('**年份**:', '').strip()
            elif line.startswith('**期刊/会议**:'):
                current_info['期刊/会议'] = line.replace('**期刊/会议**:', '').strip()
            elif line.startswith('**文献类型**:'):
                current_info['文献类型'] = line.replace('**文献类型**:', '').strip()
            elif '质量评分' in line and '分' in line:
                match = re.search(r'(\d+)分', line)
                if match:
                    current_eval['total_score'] = int(match.group(1))
            elif line.startswith('  - 相关性:'):
                match = re.search(r'相关性:\s*(\d+)', line)
                if match:
                    current_eval['relevance_score'] = int(match.group(1))
            elif line.startswith('  - 文献类型:'):
                match = re.search(r'文献类型:\s*(\d+)', line)
                if match:
                    current_eval['doc_type_score'] = int(match.group(1))
            elif line.startswith('  - 摘要质量:'):
                match = re.search(r'摘要质量:\s*(\d+)', line)
                if match:
                    current_eval['abstract_score'] = int(match.group(1))
            elif '**相关性说明**' not in line and '说明:' in line:
                current_eval['explanation'] = line.split('说明:')[1].strip() if '说明:' in line else ''
        # 保存最后一个
        if current_file and current_file not in loaded_files and current_eval.get('total_score', 0) >= SCORE_THRESHOLD:
            results_d1.append({
                'file': current_file,
                'info': current_info,
                'eval': current_eval
            })

    # 加载方向2结果（使用统一的loaded_files）
    if RESULT_FILE_2.exists():
        with open(RESULT_FILE_2, 'r', encoding='utf-8') as f:
            content = f.read()
        current_file = None
        current_info = {}
        current_eval = {}
        for line in content.split('\n'):
            if line.startswith('## '):
                if current_file and current_file not in loaded_files and current_eval.get('total_score', 0) >= SCORE_THRESHOLD:
                    results_d2.append({
                        'file': current_file,
                        'info': current_info,
                        'eval': current_eval
                    })
                current_file = line[3:].strip()
                current_info = {}
                current_eval = {}
            elif line.startswith('**标题**:'):
                current_info['标题'] = line.replace('**标题**:', '').strip()
            elif line.startswith('**作者**:'):
                current_info['作者'] = line.replace('**作者**:', '').strip()
            elif line.startswith('**年份**:'):
                current_info['年份'] = line.replace('**年份**:', '').strip()
            elif line.startswith('**期刊/会议**:'):
                current_info['期刊/会议'] = line.replace('**期刊/会议**:', '').strip()
            elif line.startswith('**文献类型**:'):
                current_info['文献类型'] = line.replace('**文献类型**:', '').strip()
            elif '质量评分' in line and '分' in line:
                match = re.search(r'(\d+)分', line)
                if match:
                    current_eval['total_score'] = int(match.group(1))
            elif line.startswith('  - 相关性:'):
                match = re.search(r'相关性:\s*(\d+)', line)
                if match:
                    current_eval['relevance_score'] = int(match.group(1))
            elif line.startswith('  - 文献类型:'):
                match = re.search(r'文献类型:\s*(\d+)', line)
                if match:
                    current_eval['doc_type_score'] = int(match.group(1))
            elif line.startswith('  - 摘要质量:'):
                match = re.search(r'摘要质量:\s*(\d+)', line)
                if match:
                    current_eval['abstract_score'] = int(match.group(1))
        if current_file and current_file not in loaded_files and current_eval.get('total_score', 0) >= SCORE_THRESHOLD:
            results_d2.append({
                'file': current_file,
                'info': current_info,
                'eval': current_eval
            })

    # 加载方向3结果（使用统一的loaded_files）
    if RESULT_FILE_3.exists():
        with open(RESULT_FILE_3, 'r', encoding='utf-8') as f:
            content = f.read()
        current_file = None
        current_info = {}
        current_eval = {}
        for line in content.split('\n'):
            if line.startswith('## '):
                if current_file and current_file not in loaded_files and current_eval.get('total_score', 0) >= SCORE_THRESHOLD:
                    results_d3.append({
                        'file': current_file,
                        'info': current_info,
                        'eval': current_eval
                    })
                current_file = line[3:].strip()
                current_info = {}
                current_eval = {}
            elif line.startswith('**标题**:'):
                current_info['标题'] = line.replace('**标题**:', '').strip()
            elif line.startswith('**作者**:'):
                current_info['作者'] = line.replace('**作者**:', '').strip()
            elif line.startswith('**年份**:'):
                current_info['年份'] = line.replace('**年份**:', '').strip()
            elif line.startswith('**期刊/会议**:'):
                current_info['期刊/会议'] = line.replace('**期刊/会议**:', '').strip()
            elif line.startswith('**文献类型**:'):
                current_info['文献类型'] = line.replace('**文献类型**:', '').strip()
            elif '质量评分' in line and '分' in line:
                match = re.search(r'(\d+)分', line)
                if match:
                    current_eval['total_score'] = int(match.group(1))
            elif line.startswith('  - 相关性:'):
                match = re.search(r'相关性:\s*(\d+)', line)
                if match:
                    current_eval['relevance_score'] = int(match.group(1))
            elif line.startswith('  - 文献类型:'):
                match = re.search(r'文献类型:\s*(\d+)', line)
                if match:
                    current_eval['doc_type_score'] = int(match.group(1))
            elif line.startswith('  - 摘要质量:'):
                match = re.search(r'摘要质量:\s*(\d+)', line)
                if match:
                    current_eval['abstract_score'] = int(match.group(1))
        if current_file and current_file not in loaded_files and current_eval.get('total_score', 0) >= SCORE_THRESHOLD:
            results_d3.append({
                'file': current_file,
                'info': current_info,
                'eval': current_eval
            })

    print(f"已加载已有结果: 方向1={len(results_d1)}篇, 方向2={len(results_d2)}篇, 方向3={len(results_d3)}篇")

def save_results(results_d1, results_d2, results_d3):
    """保存结果到 MD 文件"""

    # 方向1
    with open(RESULT_FILE_1, 'w', encoding='utf-8') as f:
        f.write("# 三峡枢纽航运经济社会效益相关文献汇总（质评版）\n\n")
        f.write(f"筛选时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("## 筛选标准\n")
        f.write(f"- 入门分数: ≥{SCORE_THRESHOLD}分\n")
        f.write("- 相关性得分(40%) + 文献类型得分(20%) + 摘要质量得分(15%)\n\n")
        f.write("---\n\n")

        for item in sorted(results_d1, key=lambda x: x['eval'].get('total_score', 0), reverse=True):
            info = item['info']
            eval_data = item['eval']
            f.write(f"## {item['file']}\n\n")
            f.write(f"**标题**: {info.get('标题', '')}\n\n")
            f.write(f"**作者**: {info.get('作者', '')}\n\n")
            f.write(f"**年份**: {info.get('年份', '')}\n\n")
            f.write(f"**期刊/会议**: {info.get('期刊/会议', '')}\n\n")
            f.write(f"**文献类型**: {info.get('文献类型', '')}\n\n")
            f.write(f"**质量评分**: {eval_data.get('total_score', 0)}分\n")
            f.write(f"  - 相关性: {eval_data.get('relevance_score', 0)}分\n")
            f.write(f"  - 文献类型: {eval_data.get('doc_type_score', 0)}分\n")
            f.write(f"  - 摘要质量: {eval_data.get('abstract_score', 0)}分\n\n")
            f.write(f"**相关性说明**: {eval_data.get('explanation', '')}\n\n")
            f.write("---\n\n")

    # 方向2
    with open(RESULT_FILE_2, 'w', encoding='utf-8') as f:
        f.write("# 三峡枢纽航运节能减排相关文献汇总（质评版）\n\n")
        f.write(f"筛选时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("## 筛选标准\n")
        f.write(f"- 入门分数: ≥{SCORE_THRESHOLD}分\n")
        f.write("- 相关性得分(40%) + 文献类型得分(20%) + 摘要质量得分(15%)\n\n")
        f.write("---\n\n")

        for item in sorted(results_d2, key=lambda x: x['eval'].get('total_score', 0), reverse=True):
            info = item['info']
            eval_data = item['eval']
            f.write(f"## {item['file']}\n\n")
            f.write(f"**标题**: {info.get('标题', '')}\n\n")
            f.write(f"**作者**: {info.get('作者', '')}\n\n")
            f.write(f"**年份**: {info.get('年份', '')}\n\n")
            f.write(f"**期刊/会议**: {info.get('期刊/会议', '')}\n\n")
            f.write(f"**文献类型**: {info.get('文献类型', '')}\n\n")
            f.write(f"**质量评分**: {eval_data.get('total_score', 0)}分\n")
            f.write(f"  - 相关性: {eval_data.get('relevance_score', 0)}分\n")
            f.write(f"  - 文献类型: {eval_data.get('doc_type_score', 0)}分\n")
            f.write(f"  - 摘要质量: {eval_data.get('abstract_score', 0)}分\n\n")
            f.write(f"**相关性说明**: {eval_data.get('explanation', '')}\n\n")
            f.write("---\n\n")

    # 方向3
    with open(RESULT_FILE_3, 'w', encoding='utf-8') as f:
        f.write("# 三峡枢纽水运通过能力相关文献汇总（质评版）\n\n")
        f.write(f"筛选时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("## 筛选标准\n")
        f.write(f"- 入门分数: ≥{SCORE_THRESHOLD}分\n")
        f.write("- 相关性得分(40%) + 文献类型得分(20%) + 摘要质量得分(15%)\n\n")
        f.write("---\n\n")

        for item in sorted(results_d3, key=lambda x: x['eval'].get('total_score', 0), reverse=True):
            info = item['info']
            eval_data = item['eval']
            f.write(f"## {item['file']}\n\n")
            f.write(f"**标题**: {info.get('标题', '')}\n\n")
            f.write(f"**作者**: {info.get('作者', '')}\n\n")
            f.write(f"**年份**: {info.get('年份', '')}\n\n")
            f.write(f"**期刊/会议**: {info.get('期刊/会议', '')}\n\n")
            f.write(f"**文献类型**: {info.get('文献类型', '')}\n\n")
            f.write(f"**质量评分**: {eval_data.get('total_score', 0)}分\n")
            f.write(f"  - 相关性: {eval_data.get('relevance_score', 0)}分\n")
            f.write(f"  - 文献类型: {eval_data.get('doc_type_score', 0)}分\n")
            f.write(f"  - 摘要质量: {eval_data.get('abstract_score', 0)}分\n\n")
            f.write(f"**相关性说明**: {eval_data.get('explanation', '')}\n\n")
            f.write("---\n\n")

if __name__ == "__main__":
    process_papers()