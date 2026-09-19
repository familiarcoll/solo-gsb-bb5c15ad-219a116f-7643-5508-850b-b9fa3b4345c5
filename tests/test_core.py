import unittest

from app.analysis import (attach_allergens, collect_allergens, compare_label,
                          diff_allergens, diff_ingredients, normalize_name,
                          trace_allergen_paths, trace_missing_sources)
from app.parser import parse_recipe


def lookup(d):
    return {normalize_name(k): set(v) for k, v in d.items()}


class ParserTest(unittest.TestCase):
    def test_nested_parens(self):
        tree = parse_recipe(
            "配料表：小麦粉，巧克力酱（可可脂，奶粉（乳清粉，乳糖），白砂糖），食盐")
        self.assertEqual([n.name for n in tree], ["小麦粉", "巧克力酱", "食盐"])
        choco = tree[1]
        self.assertEqual([c.name for c in choco.children],
                         ["可可脂", "奶粉", "白砂糖"])
        milk = choco.children[1]
        self.assertEqual([c.name for c in milk.children], ["乳清粉", "乳糖"])

    def test_english_parens_and_commas(self):
        tree = parse_recipe("flour, sauce(milk, sugar)")
        self.assertEqual([n.name for n in tree], ["flour", "sauce"])
        self.assertEqual([c.name for c in tree[1].children], ["milk", "sugar"])

    def test_anonymous_group(self):
        tree = parse_recipe("小麦粉，（甲，乙），食盐")
        self.assertEqual([n.name for n in tree], ["小麦粉", "甲", "乙", "食盐"])

    def test_empty_and_prefix(self):
        self.assertEqual(parse_recipe(""), [])
        self.assertEqual(parse_recipe("Ingredients: flour, sugar")[0].name, "flour")

    def test_unbalanced_paren_tolerated(self):
        tree = parse_recipe("小麦粉，酱（甲，乙")
        self.assertEqual(len(tree), 2)
        self.assertEqual([c.name for c in tree[1].children], ["甲", "乙"])


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        self.dictionary = lookup({
            "小麦粉": ["麸质谷物"],
            "乳清粉": ["乳"],
            "乳糖": ["乳"],
            "可可脂": [],
            "大豆卵磷脂": ["大豆"],
        })

    def test_propagation_through_levels(self):
        tree = parse_recipe("小麦粉，巧克力酱（可可脂，奶粉（乳清粉，乳糖），大豆卵磷脂）")
        attach_allergens(tree, self.dictionary)
        choco = tree[1]
        self.assertEqual(choco.total_allergens, {"乳", "大豆"})
        self.assertEqual(choco.own_allergens, set())  # 复合原料自身无标注
        self.assertEqual(collect_allergens(tree), {"乳", "大豆", "麸质谷物"})

    def test_unknown_leaf_flagged(self):
        tree = parse_recipe("小麦粉，神秘香精")
        attach_allergens(tree, self.dictionary)
        self.assertFalse(tree[0].unknown)
        self.assertTrue(tree[1].unknown)

    def test_compound_not_flagged_unknown(self):
        tree = parse_recipe("酱（乳清粉）")
        attach_allergens(tree, self.dictionary)
        self.assertFalse(tree[0].unknown)  # 复合原料不提示未识别
        self.assertEqual(tree[0].total_allergens, {"乳"})

    def test_compare_label(self):
        res = compare_label({"乳", "大豆"}, {"乳", "花生"})
        self.assertEqual(res["missing"], ["大豆"])
        self.assertEqual(res["over_reported"], ["花生"])
        self.assertEqual(res["matched"], ["乳"])

    def test_diff_allergens(self):
        d = diff_allergens({"乳", "大豆"}, {"乳", "芝麻"})
        self.assertEqual(d["added"], ["芝麻"])
        self.assertEqual(d["removed"], ["大豆"])
        self.assertEqual(d["unchanged"], ["乳"])

    def test_diff_ingredients(self):
        dictionary = lookup({"奶粉": ["乳"], "芝麻": ["芝麻"], "小麦粉": ["麸质谷物"]})
        tree_a = parse_recipe("小麦粉，酱（奶粉）")
        tree_b = parse_recipe("小麦粉，酱（芝麻），新配料（奶粉）")
        attach_allergens(tree_a, dictionary)
        attach_allergens(tree_b, dictionary)
        changes = diff_ingredients(tree_a, tree_b)
        by_name = {c["ingredient"]: c for c in changes}
        self.assertEqual(by_name["酱"]["change"], "modified")
        self.assertEqual(by_name["酱"]["added"], ["芝麻"])
        self.assertEqual(by_name["酱"]["removed"], ["乳"])
        self.assertEqual(by_name["新配料"]["change"], "added")


class MissingTraceTest(unittest.TestCase):
    """遗漏来源追踪：为遗漏过敏原保留其在配料树中的全部来源路径。"""

    def test_deep_nested_paths(self):
        # 多层嵌套：路径完整保留“顶层配料 > … > 实际贡献原料”的链条
        dictionary = lookup({
            "小麦粉": ["麸质谷物"],
            "乳清粉": ["乳"],
            "乳糖": ["乳"],
            "大豆卵磷脂": ["大豆"],
            "可可脂": [],
        })
        tree = parse_recipe(
            "小麦粉，巧克力酱（可可脂，奶粉（乳清粉，乳糖），大豆卵磷脂）")
        attach_allergens(tree, dictionary)
        traces = trace_missing_sources(tree, ["乳", "大豆"])
        self.assertEqual(traces["乳"], [
            ["巧克力酱", "奶粉", "乳清粉"],
            ["巧克力酱", "奶粉", "乳糖"],
        ])
        self.assertEqual(traces["大豆"], [["巧克力酱", "大豆卵磷脂"]])

    def test_same_name_branches_not_merged(self):
        # 同名原料出现在不同分支：路径各自保留，不得合并
        dictionary = lookup({"乳清粉": ["乳"]})
        tree = parse_recipe("巧克力酱（乳清粉），奶茶（乳清粉）")
        attach_allergens(tree, dictionary)
        self.assertEqual(trace_allergen_paths(tree, "乳"),
                         [["奶茶", "乳清粉"], ["巧克力酱", "乳清粉"]])

    def test_same_name_nested_branches_not_merged(self):
        # 同名原料嵌套出现在不同分支的深层位置，同样不合并
        dictionary = lookup({"乳清粉": ["乳"]})
        tree = parse_recipe("酱（甲（乳清粉）），酱（乙（乳清粉））")
        attach_allergens(tree, dictionary)
        self.assertEqual(trace_allergen_paths(tree, "乳"),
                         [["酱", "乙", "乳清粉"], ["酱", "甲", "乳清粉"]])

    def test_own_annotation_terminates_path(self):
        # 节点自身标注时路径终止于该节点，不再向下延伸
        dictionary = lookup({"奶粉": ["乳"], "白砂糖": []})
        tree = parse_recipe("巧克力酱（奶粉（白砂糖））")
        attach_allergens(tree, dictionary)
        self.assertEqual(trace_allergen_paths(tree, "乳"),
                         [["巧克力酱", "奶粉"]])

    def test_own_and_inherited_both_kept(self):
        # 自身标注与子配料传播并存：两类路径都保留
        dictionary = lookup({"奶粉": ["乳"], "乳清粉": ["乳"]})
        tree = parse_recipe("奶粉（乳清粉）")
        attach_allergens(tree, dictionary)
        self.assertEqual(trace_allergen_paths(tree, "乳"),
                         [["奶粉"], ["奶粉", "乳清粉"]])

    def test_duplicate_siblings_deduped(self):
        # 同一位置重复出现的同名原料产生相同路径，结果去重
        dictionary = lookup({"乳清粉": ["乳"]})
        tree = parse_recipe("酱（乳清粉，乳清粉）")
        attach_allergens(tree, dictionary)
        self.assertEqual(trace_allergen_paths(tree, "乳"), [["酱", "乳清粉"]])

    def test_output_ordering_stable(self):
        # 输出稳定排序：与遗漏集合的传入顺序无关
        dictionary = lookup({"乳清粉": ["乳"], "芝麻": ["芝麻"]})
        tree = parse_recipe("芝麻酱（芝麻），奶粉（乳清粉）")
        attach_allergens(tree, dictionary)
        t1 = trace_missing_sources(tree, ["乳", "芝麻"])
        t2 = trace_missing_sources(tree, ["芝麻", "乳"])
        self.assertEqual(t1, t2)
        self.assertEqual(list(t1.keys()), ["乳", "芝麻"])
        self.assertEqual(t1["乳"], [["奶粉", "乳清粉"]])
        self.assertEqual(t1["芝麻"], [["芝麻酱", "芝麻"]])

    def test_absent_allergen_has_no_paths(self):
        # 配方中不存在的过敏原没有来源路径
        dictionary = lookup({"小麦粉": ["麸质谷物"]})
        tree = parse_recipe("小麦粉")
        attach_allergens(tree, dictionary)
        self.assertEqual(trace_allergen_paths(tree, "花生"), [])


if __name__ == "__main__":
    unittest.main()
