create extension if not exists pgcrypto;

create table if not exists public.users (
    telegram_id bigint primary key,
    username text,
    first_name text not null,
    language text not null default 'km' check (language in ('km', 'en')),
    created_at timestamptz not null default now()
);

create table if not exists public.bot_settings (
    key text primary key,
    value text not null default ''
);

create table if not exists public.categories (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    description text not null default '',
    active boolean not null default true,
    sort_order integer not null default 0
);

create table if not exists public.products (
    id uuid primary key default gen_random_uuid(),
    category_id uuid not null references public.categories(id),
    name text not null,
    description text not null default '',
    price numeric(10, 2) not null check (price > 0),
    reseller_price numeric(10, 2),
    image_url text,
    video_url text,
    active boolean not null default true,
    sort_order integer not null default 0
);

alter table public.products add column if not exists image_url text;
alter table public.products add column if not exists video_url text;
alter table public.products add column if not exists reseller_price numeric(10, 2);
update public.products set reseller_price = price where reseller_price is null;
alter table public.products alter column reseller_price set not null;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.products'::regclass
          and conname = 'products_reseller_price_check'
    ) then
        alter table public.products
            add constraint products_reseller_price_check
            check (reseller_price > 0 and reseller_price <= price);
    end if;
end;
$$;

create table if not exists public.resellers (
    telegram_id bigint primary key check (telegram_id > 0),
    created_at timestamptz not null default now()
);

create table if not exists public.orders (
    id uuid primary key default gen_random_uuid(),
    chat_id bigint not null,
    product_id uuid not null references public.products(id),
    quantity integer not null check (quantity > 0),
    total numeric(10, 2) not null,
    status text not null default 'pending' check (status in ('pending', 'paid', 'expired', 'failed', 'cancelled')),
    transaction_id text unique,
    payment_url text,
    created_at timestamptz not null default now(),
    delivery_claimed_at timestamptz,
    delivered_at timestamptz
);

alter table public.orders add column if not exists message_id bigint;

do $$
begin
    if exists (
        select 1 from information_schema.columns
        where table_schema = 'public' and table_name = 'orders' and column_name = 'payment_message_id'
    ) then
        execute 'update public.orders set message_id = payment_message_id where message_id is null and payment_message_id is not null';
    end if;
end;
$$;

create table if not exists public.stock_items (
    id uuid primary key default gen_random_uuid(),
    product_id uuid not null references public.products(id),
    credential_cipher text not null,
    credential_fingerprint text not null,
    status text not null default 'available' check (status in ('available', 'reserved', 'sold')),
    order_id uuid references public.orders(id),
    created_at timestamptz not null default now(),
    unique(product_id, credential_fingerprint)
);

alter table public.stock_items
    drop constraint if exists stock_items_product_id_credential_fingerprint_key;

create index if not exists stock_available_idx on public.stock_items(product_id) where status = 'available';
create unique index if not exists stock_active_fingerprint_idx
    on public.stock_items(product_id, credential_fingerprint)
    where status in ('available', 'reserved');
create index if not exists orders_pending_idx on public.orders(status, created_at) where status = 'pending';

create or replace function public.import_stock_items(p_product_id uuid, p_items jsonb)
returns integer language plpgsql security definer set search_path = public as $$
declare
    v_added integer;
begin
    if jsonb_typeof(p_items) is distinct from 'array' then raise exception 'Stock items must be a JSON array'; end if;

    insert into stock_items(product_id, credential_cipher, credential_fingerprint)
    select p_product_id, item.credential_cipher, item.credential_fingerprint
    from jsonb_to_recordset(p_items) as item(
        credential_cipher text,
        credential_fingerprint text
    )
    on conflict (product_id, credential_fingerprint)
        where status in ('available', 'reserved')
        do nothing;

    get diagnostics v_added = row_count;
    return v_added;
end;
$$;

alter table public.users enable row level security;
alter table public.resellers enable row level security;
alter table public.bot_settings enable row level security;
alter table public.categories enable row level security;
alter table public.products enable row level security;
alter table public.orders enable row level security;
alter table public.stock_items enable row level security;

create or replace function public.reserve_order(p_chat_id bigint, p_product_id uuid, p_quantity integer)
returns jsonb language plpgsql security definer set search_path = public as $$
declare
    v_order_id uuid;
    v_price numeric(10, 2);
    v_stock_ids uuid[];
begin
    if p_quantity < 1 or p_quantity > 3 then raise exception 'Quantity must be between 1 and 3'; end if;
    select case
        when exists (select 1 from resellers where telegram_id = p_chat_id)
            then reseller_price
        else price
    end
    into v_price
    from products
    where id = p_product_id and active = true;
    if v_price is null then raise exception 'Product is not available'; end if;
    select array_agg(id) into v_stock_ids from (
        select id from stock_items where product_id = p_product_id and status = 'available'
        order by created_at for update skip locked limit p_quantity
    ) available;
    if coalesce(array_length(v_stock_ids, 1), 0) < p_quantity then raise exception 'Insufficient stock'; end if;
    insert into orders(chat_id, product_id, quantity, total)
    values (p_chat_id, p_product_id, p_quantity, v_price * p_quantity) returning id into v_order_id;
    update stock_items set status = 'reserved', order_id = v_order_id where id = any(v_stock_ids);
    return jsonb_build_object('id', v_order_id, 'total', v_price * p_quantity);
end;
$$;

create or replace function public.set_order_payment(p_order_id uuid, p_transaction_id text, p_payment_url text)
returns void language plpgsql security definer set search_path = public as $$
begin
    update orders set transaction_id = p_transaction_id, payment_url = p_payment_url
    where id = p_order_id and status = 'pending';
    if not found then raise exception 'Pending order not found'; end if;
end;
$$;

create or replace function public.release_order(p_order_id uuid, p_status text)
returns void language plpgsql security definer set search_path = public as $$
begin
    if p_status not in ('cancelled', 'expired', 'failed') then raise exception 'Invalid release status'; end if;
    update orders set status = p_status where id = p_order_id and status = 'pending';
    update stock_items set status = 'available', order_id = null
    where order_id = p_order_id and status = 'reserved';
end;
$$;

create or replace function public.fulfill_order(p_order_id uuid)
returns text[] language plpgsql security definer set search_path = public as $$
declare
    v_status text;
    v_delivered_at timestamptz;
    v_delivery_claimed_at timestamptz;
    v_credentials text[];
begin
    select status, delivered_at, delivery_claimed_at
    into v_status, v_delivered_at, v_delivery_claimed_at
    from orders where id = p_order_id for update;
    if v_status = 'pending' then
        update orders set status = 'paid' where id = p_order_id;
        update stock_items set status = 'sold' where order_id = p_order_id and status = 'reserved';
    elsif v_status <> 'paid' then
        return array[]::text[];
    end if;
    if v_delivered_at is not null then return array[]::text[]; end if;
    if v_delivery_claimed_at > now() - interval '5 minutes' then return array[]::text[]; end if;
    update orders set delivery_claimed_at = now() where id = p_order_id;
    select array_agg(credential_cipher order by created_at) into v_credentials
    from stock_items where order_id = p_order_id and status = 'sold';
    return coalesce(v_credentials, array[]::text[]);
end;
$$;

create or replace function public.mark_order_delivered(p_order_id uuid)
returns void language sql security definer set search_path = public as $$
    update orders set delivered_at = now(), delivery_claimed_at = null
    where id = p_order_id and status = 'paid' and delivered_at is null;
$$;

create or replace function public.available_stock_count(p_product_id uuid)
returns integer language sql stable security definer set search_path = public as $$
    select count(*)::integer from stock_items where product_id = p_product_id and status = 'available';
$$;

create or replace function public.sales_summary()
returns jsonb language sql stable security definer set search_path = public as $$
    select jsonb_build_object(
        'paid_orders', count(*),
        'total_revenue', coalesce(sum(total), 0),
        'today_revenue', coalesce(sum(total) filter (where created_at >= current_date), 0)
    ) from orders where status = 'paid';
$$;

revoke all on function public.reserve_order(bigint, uuid, integer) from public, anon, authenticated;
revoke all on function public.set_order_payment(uuid, text, text) from public, anon, authenticated;
revoke all on function public.release_order(uuid, text) from public, anon, authenticated;
revoke all on function public.fulfill_order(uuid) from public, anon, authenticated;
revoke all on function public.mark_order_delivered(uuid) from public, anon, authenticated;
revoke all on function public.available_stock_count(uuid) from public, anon, authenticated;
revoke all on function public.sales_summary() from public, anon, authenticated;
revoke all on function public.import_stock_items(uuid, jsonb) from public, anon, authenticated;
grant execute on function public.reserve_order(bigint, uuid, integer) to service_role;
grant execute on function public.set_order_payment(uuid, text, text) to service_role;
grant execute on function public.release_order(uuid, text) to service_role;
grant execute on function public.fulfill_order(uuid) to service_role;
grant execute on function public.mark_order_delivered(uuid) to service_role;
grant execute on function public.available_stock_count(uuid) to service_role;
grant execute on function public.sales_summary() to service_role;
grant execute on function public.import_stock_items(uuid, jsonb) to service_role;
