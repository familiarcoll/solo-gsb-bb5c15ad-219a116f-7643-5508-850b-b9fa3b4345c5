"""端到端集成冒烟测试：不依赖 FastAPI，直接走 db + parser + analysis + report 全链路。"""
import os
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "smoke.db")

from app import db
from app.analysis import (attach_allergens, collect_allergens, compare_label,
                          diff_allergens, diff_ingredients, flatten,
                          trace_missing_sources)
from app.parser import parse_recipe
from app.report import render_html_report, render_markdown_report

db.init_db()

# 1. 导入字典
n = db.bulk_import("""\
小麦粉：麸质谷物
乳清粉：乳
乳糖：乳
奶粉：乳
可可脂：
大豆卵磷脂：大豆
鸡蛋：蛋类
椰子粉：坚果
芝麻：芝麻
食盐：
""")
assert n == 10, n
print(f"1. 字典导入 {n} 条 OK")

# 2. 两版配方
TEXT_A = "配料表：小麦粉，白砂糖，巧克力酱（可可脂，奶粉（乳清粉，乳糖），大豆卵磷脂），鸡蛋，食盐"
TEXT_B = "配料表：小麦粉，白砂糖，巧克力酱（可可脂，椰子粉），鸡蛋，芝麻酱（芝麻），食盐"
DECLARED = ["麸质谷物", "蛋类"]

dictionary = db.dict_lookup()

def build_version(tree):
    return {
        "tree": [x.to_dict() for x in tree],
        "allergens": sorted(collect_allergens(tree)),
        "unknown_ingredients": sorted({x.name for x in flatten(tree) if x.unknown}),
    }

tree_a = parse_recipe(TEXT_A)
attach_allergens(tree_a, dictionary)
va = build_version(tree_a)
tree_b = parse_recipe(TEXT_B)
attach_allergens(tree_b, dictionary)
vb = build_version(tree_b)

assert set(va["allergens"]) == {"麸质谷物", "乳", "大豆", "蛋类"}, va["allergens"]
assert set(vb["allergens"]) == {"麸质谷物", "坚果", "蛋类", "芝麻"}, vb["allergens"]
assert "白砂糖" in va["unknown_ingredients"]  # 未登记原料被标记
print("2. 两版配方解析与过敏原传播 OK")
print("   A 版过敏原:", va["allergens"])
print("   B 版过敏原:", vb["allergens"])

# 3. 标签核对（对照 B 版）+ 遗漏来源追踪
check = compare_label(vb["allergens"], DECLARED)
check["missing_traces"] = trace_missing_sources(tree_b, check["missing"])
assert check["missing"] == ["坚果", "芝麻"], check
assert check["over_reported"] == [], check
assert check["matched"] == ["蛋类", "麸质谷物"], check
# 遗漏过敏原的来源路径：完整保留嵌套链条
assert check["missing_traces"]["坚果"] == [["巧克力酱", "椰子粉"]], check["missing_traces"]
assert check["missing_traces"]["芝麻"] == [["芝麻酱", "芝麻"]], check["missing_traces"]
print("3. 标签核对 OK  遗漏:", check["missing"], " 多报:", check["over_reported"])
print("   遗漏来源路径:", check["missing_traces"])

# 4. 版本对比
diff = diff_allergens(va["allergens"], vb["allergens"])
assert diff["added"] == ["坚果", "芝麻"]
assert diff["removed"] == ["乳", "大豆"]
changes = diff_ingredients(tree_a, tree_b)
names = {c["ingredient"]: c["change"] for c in changes}
assert names.get("巧克力酱") == "modified" and names.get("芝麻酱") == "added", names
print("4. 版本对比 OK  新增:", diff["added"], " 减少:", diff["removed"])

# 5. 入库 + 报告导出
result = {
    "version_a": va, "version_b": vb, "diff": diff,
    "ingredient_changes": changes,
    "label_check": check, "label_check_version": "新版配方",
}
rid = db.create_review(name="巧克力饼干 配方变更审查", declared=DECLARED,
                       text_a=TEXT_A, text_b=TEXT_B,
                       label_a="现版配方", label_b="新版配方", result=result)
review = db.get_review(rid)
# 历史记录重新打开：遗漏来源路径仍在审查结果中
assert review["result"]["label_check"]["missing_traces"] == check["missing_traces"]
html_report = render_html_report(review)
md_report = render_markdown_report(review)
assert "标签遗漏" in html_report and "坚果" in html_report and "芝麻" in html_report
assert "遗漏来源追踪" in html_report and "巧克力酱 &gt; 椰子粉" in html_report
assert "芝麻酱 &gt; 芝麻" in html_report
assert "# 食品过敏原标签审查报告" in md_report and "新增过敏原" in md_report
assert "遗漏来源追踪" in md_report and "巧克力酱 > 椰子粉" in md_report
assert "芝麻酱 > 芝麻" in md_report
assert len(db.list_reviews()) == 1
print(f"5. 审查记录入库 (#{rid}) 与 HTML/Markdown 报告导出 OK（含遗漏来源路径）")

# 6. 同名原料不同分支：来源路径不合并，且输出稳定去重排序
db.bulk_import("乳清粉：乳")
tree = parse_recipe("巧克力酱（乳清粉），奶茶（奶粉（乳清粉）），巧克力酱（乳清粉）")
attach_allergens(tree, db.dict_lookup())
traces = trace_missing_sources(tree, ["乳"])
# 字典中“奶粉”自身标注了乳：自身标注与传播路径并存；
# 两个顶层“巧克力酱”产生的相同路径被去重，同名分支不合并
assert traces["乳"] == [
    ["奶茶", "奶粉"],
    ["奶茶", "奶粉", "乳清粉"],
    ["巧克力酱", "乳清粉"],
], traces
print("6. 同名分支路径不合并、重复路径去重排序 OK:", traces["乳"])

print("\n全部集成冒烟测试通过 ✔")
