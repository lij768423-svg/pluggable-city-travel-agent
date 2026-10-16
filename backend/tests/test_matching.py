from app.matching import match_name

NAMES = {0: "灵隐寺", 1: "杭州宋城", 2: "西湖风景名胜区", 3: "雷峰塔", 4: "浙江省博物馆(之江馆区)"}


def test_exact_and_prefix():
    assert match_name("灵隐寺", NAMES) == 0
    assert match_name("宋城", NAMES) == 1
    assert match_name("杭州宋城景区", NAMES) == 1


def test_contains_and_unknown():
    assert match_name("浙江省博物馆", NAMES) == 4
    assert match_name("西湖", NAMES) == 2  # 从宽匹配
    assert match_name("杭州迪士尼乐园", NAMES) is None


def test_variants_parentheses_and_dots():
    names = {7: "城隍阁景区", 8: "清河坊街"}
    assert match_name("城隍阁（吴山天风）", names) == 7
    assert match_name("南宋御街·清河坊街", names) == 8
