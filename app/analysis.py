"""过敏原传播、标签核对与版本比对。"""
from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Set

from .parser import IngredientNode


def normalize_name(name: str) -> str:
    """原料名归一化：去空白、转小写，便于字典匹配。"""
    return "".join(name.split()).lower()


def attach_allergens(nodes: List[IngredientNode],
                     dictionary: Dict[str, Set[str]]) -> List[IngredientNode]:
    """按字典标注每种原料的过敏原，并把子配料的过敏原向上传播。

    dictionary 的 key 必须是 normalize_name 之后的形式。
    """
    for node in nodes:
        key = normalize_name(node.name)
        node.own_allergens = set(dictionary.get(key, set()))
        attach_allergens(node.children, dictionary)
        total = set(node.own_allergens)
        for child in node.children:
            total |= child.total_allergens
        node.total_allergens = total
        # 只有叶子原料才提示“未识别”；复合原料的过敏原来自子配料
        node.unknown = key not in dictionary and not node.children
    return nodes


def collect_allergens(nodes: List[IngredientNode]) -> Set[str]:
    """整份配方的过敏原全集（各顶层配料传播结果的并集）。"""
    result: Set[str] = set()
    for node in nodes:
        result |= node.total_allergens
    return result


def flatten(nodes: List[IngredientNode]) -> List[IngredientNode]:
    """把树拍平成列表（先序遍历）。"""
    out: List[IngredientNode] = []
    for node in nodes:
        out.append(node)
        out.extend(flatten(node.children))
    return out


def compare_label(computed: Iterable[str], declared: Iterable[str]) -> dict:
    """标签核对：计算值 vs 标签声明值。

    missing       —— 配方含有但标签未声明（遗漏，必须补充）；
    over_reported —— 标签声明了但配方中不存在（多报）；
    matched       —— 两边一致。
    """
    computed, declared = set(computed), set(declared)
    return {
        "missing": sorted(computed - declared),
        "over_reported": sorted(declared - computed),
        "matched": sorted(computed & declared),
    }


def trace_allergen_paths(nodes: Sequence[IngredientNode],
                         allergen: str) -> List[List[str]]:
    """追踪某过敏原在配料树中的全部来源路径。

    每条路径是从顶层配料到“实际贡献节点”的原料名序列：
    - 节点自身标注了该过敏原 → 路径终止于该节点；
    - 过敏原由子配料传播上来 → 路径继续向下延伸到实际贡献的后代节点；
    - 自身标注与子配料传播并存时，两类路径都保留。

    同名原料出现在不同分支时各自保留完整路径，不做合并；
    同一位置重复出现的同名原料产生的相同路径会被去重，
    最终结果按路径稳定排序，保证 API 输出可复现。
    """
    paths: List[List[str]] = []

    def walk(node: IngredientNode, prefix: List[str]) -> None:
        if allergen not in node.total_allergens:
            return
        current = prefix + [node.name]
        if allergen in node.own_allergens:
            paths.append(current)
        for child in node.children:
            walk(child, current)

    for node in nodes:
        walk(node, [])

    unique = sorted({tuple(p) for p in paths})
    return [list(p) for p in unique]


def trace_missing_sources(nodes: Sequence[IngredientNode],
                          missing: Iterable[str]) -> Dict[str, List[List[str]]]:
    """为每个遗漏的过敏原计算来源路径，键按过敏原名稳定排序。"""
    return {a: trace_allergen_paths(nodes, a) for a in sorted(set(missing))}


def diff_allergens(old: Iterable[str], new: Iterable[str]) -> dict:
    """两版配方的过敏原集合差异。"""
    old, new = set(old), set(new)
    return {
        "added": sorted(new - old),
        "removed": sorted(old - new),
        "unchanged": sorted(old & new),
    }


def diff_ingredients(tree_a: List[IngredientNode],
                     tree_b: List[IngredientNode]) -> List[dict]:
    """按顶层配料名比对两版配方，给出每种配料的过敏原变化。"""
    map_a = {normalize_name(n.name): n for n in tree_a}
    map_b = {normalize_name(n.name): n for n in tree_b}
    changes: List[dict] = []
    for key in sorted(set(map_a) | set(map_b)):
        na, nb = map_a.get(key), map_b.get(key)
        name = (nb or na).name
        if na is None:
            changes.append({"ingredient": name, "change": "added",
                            "allergens": sorted(nb.total_allergens)})
        elif nb is None:
            changes.append({"ingredient": name, "change": "removed",
                            "allergens": sorted(na.total_allergens)})
        else:
            added = nb.total_allergens - na.total_allergens
            removed = na.total_allergens - nb.total_allergens
            if added or removed:
                changes.append({"ingredient": name, "change": "modified",
                                "added": sorted(added),
                                "removed": sorted(removed)})
    return changes
