import re
from pathlib import Path


def test_reseller_price_is_positive_without_being_capped_by_customer_price():
    schema = (Path(__file__).parents[1] / "schema.sql").read_text(
        encoding="utf-8"
    )

    assert "drop constraint if exists products_reseller_price_check" in schema
    assert "check (reseller_price > 0)" in schema
    assert "reseller_price <= price" not in schema


def test_schema_stores_after_order_content_on_catalogs_and_migrates_consistent_values():
    schema = (Path(__file__).parents[1] / "schema.sql").read_text(
        encoding="utf-8"
    )
    migration = (
        Path(__file__).parents[1] / "scripts" / "fix_catalog_delivery_columns.sql"
    ).read_text(encoding="utf-8")

    assert "delivery_message text not null default ''" in schema
    assert re.search(
        r"alter table public\.categories\s+add column if not exists media_url text",
        schema,
    )
    assert "add column if not exists media_url text" in migration
    assert "notify pgrst, 'reload schema';" in migration
    assert re.search(
        r"alter table public\.categories\s+add column if not exists delivery_media_url text",
        schema,
    )
    assert "count(distinct (delivery_message, delivery_media_url)) = 1" in schema


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


def test_wallet_deposits_allow_cancellation_and_restrict_cancel_rpc():
    schema = (Path(__file__).parents[1] / "schema.sql").read_text(
        encoding="utf-8"
    )

    assert (
        "check (status in ('pending', 'paid', 'expired', 'failed', 'cancelled'))"
        in schema
    )
    assert (
        "create or replace function public.cancel_wallet_deposit(p_deposit_id uuid)"
        in schema
    )
    assert (
        "where id = p_deposit_id and status = 'pending';\n    return found;" in schema
    )
    assert (
        "revoke all on function public.cancel_wallet_deposit(uuid) "
        "from public, anon, authenticated;"
    ) in schema
    assert (
        "grant execute on function public.cancel_wallet_deposit(uuid) to service_role;"
        in schema
    )
