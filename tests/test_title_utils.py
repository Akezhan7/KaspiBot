from parser.title_utils import clean_product_title


def test_clean_product_title_rejects_numeric_sku_names():
    assert clean_product_title("Товар 12345678") is None
    assert clean_product_title("SKU 109619826") is None
    assert clean_product_title("арт. 113282830") is None
    assert clean_product_title("104886899") is None


def test_clean_product_title_rejects_empty_and_unknown_values():
    assert clean_product_title(None) is None
    assert clean_product_title("") is None
    assert clean_product_title("Без названия") is None


def test_clean_product_title_accepts_real_product_names():
    assert clean_product_title("Клеевой пистолет 20 w 7 мм") == "Клеевой пистолет 20 w 7 мм"
    assert clean_product_title(" Samsung Galaxy S24 ") == "Samsung Galaxy S24"
    assert clean_product_title("iPhone 15 256GB") == "iPhone 15 256GB"
