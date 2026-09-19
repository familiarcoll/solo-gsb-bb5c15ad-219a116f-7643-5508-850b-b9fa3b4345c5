import unittest

from app.analysis import (attach_allergens, collect_allergens, compare_label,
                          diff_allergens, diff_ingredients, normalize_name)
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


if __name__ == "__main__":
    unittest.main()
