"""
根据已有搜索结果下载 PDF
无需重新搜索，直接从现有结果文件中提取 DOI 并下载
"""

import json
import re
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from pdf_downloader import PDFDownloader
from config import PAPERS_DIR


def parse_search_results(file_path: Path):
    """
    解析搜索结果文件，提取 DOI 和标题

    格式示例:
    [1] Paper Title
        作者: Author1, Author2
        年份: 2020, 引用: 100
        DOI: 10.1234/example
    """
    items = []

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"读取文件失败: {e}")
        return items

    # 按行分割
    lines = content.split("\n")

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 检测文献编号 [1], [2] 等
        match = re.match(r'\[(\d+)\]\s+(.+)', line)
        if match:
            title = match.group(2).strip()

            # 继续读取后面的行来找 DOI
            doi = None
            j = i + 1
            while j < len(lines) and j < i + 10:  # 最多向下读取10行
                next_line = lines[j].strip()

                # 查找 DOI 行
                if "DOI:" in next_line:
                    doi_match = re.search(r'10\.\d{4,}/[^\s]+', next_line)
                    if doi_match:
                        doi = doi_match.group(0)
                        # 清理 DOI
                        doi = doi.strip().rstrip(',').rstrip('.')

                # 遇到下一个文献编号，停止
                if re.match(r'\[(\d+)\]', next_line):
                    break

                j += 1

            if doi:
                items.append({"doi": doi, "title": title})

            i = j
        else:
            i += 1

    return items


def download_direction(direction: str, max_download: int = None):
    """
    下载指定方向的 PDF

    Args:
        direction: 方向标识 (direction_1, direction_2, direction_3)
        max_download: 最大下载数量，None 表示全部
    """
    direction_dir = PAPERS_DIR / direction

    if not direction_dir.exists():
        print(f"方向目录不存在: {direction_dir}")
        return

    results_file = direction_dir / "search_results.txt"

    if not results_file.exists():
        print(f"搜索结果文件不存在: {results_file}")
        return

    # 解析搜索结果
    print(f"解析搜索结果: {results_file}")
    items = parse_search_results(results_file)

    if not items:
        print("未找到可下载的文献")
        return

    print(f"找到 {len(items)} 篇文献")

    if max_download:
        items = items[:max_download]

    # 下载
    print(f"\n开始下载 {len(items)} 篇 PDF...")

    downloader = PDFDownloader(save_dir=direction_dir)

    results = downloader.download_batch(items)

    # 统计结果
    success = sum(1 for r in results if r["success"])
    failed = len(results) - success

    print(f"\n下载完成!")
    print(f"  成功: {success}/{len(results)}")
    print(f"  失败: {failed}/{len(results)}")

    # 保存下载结果
    output_file = direction_dir / "download_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"下载结果已保存到: {output_file}")

    # 显示失败的 DOI
    if failed > 0:
        print("\n下载失败的文献:")
        for r in results:
            if not r["success"]:
                print(f"  - {r.get('title', 'Unknown')[:50]}...")
                print(f"    DOI: {r.get('doi', 'N/A')}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="根据已有搜索结果下载 PDF")
    parser.add_argument("-d", "--direction", type=str, default="all",
                        help="研究方向: direction_1, direction_2, direction_3, all")
    parser.add_argument("-m", "--max", type=int, default=None,
                        help="最大下载数量")

    args = parser.parse_args()

    directions_map = {
        "1": "direction_1",
        "2": "direction_2",
        "3": "direction_3",
        "all": ["direction_1", "direction_2", "direction_3"]
    }

    # 解析方向
    directions = directions_map.get(args.direction, ["direction_1"])

    if isinstance(directions, str):
        directions = [directions]

    direction_names = {
        "direction_1": "经济社会效益",
        "direction_2": "节能减排",
        "direction_3": "水运通过能力"
    }

    for direction in directions:
        name = direction_names.get(direction, direction)
        print(f"\n{'='*60}")
        print(f"下载方向: {name} ({direction})")
        print(f"{'='*60}")
        download_direction(direction, args.max)

    print(f"\n所有下载完成!")


if __name__ == "__main__":
    main()