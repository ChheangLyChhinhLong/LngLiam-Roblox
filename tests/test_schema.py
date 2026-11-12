import re
from pathlib import Path


def test_set_order_payment_permissions_match_function_signature():
    schema = (Path(__file__).parents[1] / "schema.sql").read_text(
        encoding="utf-8"
    )
    definition = re.search(
        r"create or replace function public\.set_order_payment\((.*?)\)\s*returns",
        schema,
        re.DOTALL,
    )
    assert definition is not None
    expected_types = tuple(
        parameter.strip().split()[-1]
        for parameter in definition.group(1).split(",")
    )

    permissions = re.findall(
        r"(?:revoke all on function|grant execute on function) "
        r"public\.set_order_payment\(([^)]*)\)",
        schema,
    )
    assert len(permissions) == 2
    assert all(
        tuple(argument.strip() for argument in signature.split(","))
        == expected_types
        for signature in permissions
    )
