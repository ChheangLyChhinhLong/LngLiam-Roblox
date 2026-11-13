do $$
declare
    v_category_id uuid;
begin
    select id
    into strict v_category_id
    from public.categories
    where lower(trim(name)) = lower(trim('ABCD PANEL - NONROOT'));

    with packages(name, price, display_order) as (
        values
            ('12h', 0.43::numeric, 1),
            ('1 Day', 0.77::numeric, 2),
            ('3 Days', 1.66::numeric, 3),
            ('7 Days', 2.21::numeric, 4)
    )
    insert into public.products (
        category_id,
        name,
        description,
        price,
        reseller_price,
        active,
        sort_order
    )
    select
        v_category_id,
        packages.name,
        '',
        packages.price,
        packages.price,
        true,
        existing_products.last_sort_order + packages.display_order
    from packages
    cross join (
        select coalesce(max(sort_order), 0) as last_sort_order
        from public.products
        where category_id = v_category_id
    ) as existing_products
    where not exists (
        select 1
        from public.products existing
        where existing.category_id = v_category_id
          and lower(trim(existing.name)) = lower(trim(packages.name))
    )
    order by packages.display_order;
end
$$;