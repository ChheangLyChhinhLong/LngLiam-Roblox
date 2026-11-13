with catalogs(name, display_order) as (
    values
        ('ABCD PANEL - NONROOT', 1),
        ('AIMHACK - IOS-ANDROID', 2),
        ('Angry Mods Root', 3),
        ('CTB MODS - NONROOT', 4),
        ('DRIP CLIENT APKMOD', 5),
        ('DRIP CLIENT PROXY', 6),
        ('Drip Client Root', 7),
        ('Drip Wire - IOS+Android', 8),
        ('Guild Glory Bot', 9),
        ('Hg Cheats Apkmod', 10),
        ('Myth Panel Android+IOS', 11),
        ('NINEX PANEL - NONROOT', 12),
        ('Phantom - Android+IOS', 13),
        ('Prime Hook - Apkmod', 14),
        ('Rapid Core Panel', 15),
        ('REZZX MODS - Android+IOS', 16),
        ('Xreg - Android+IOS', 17),
        ('XYZ Cheats Apk', 18),
        ('Zrex - Root+NonRoot', 19)
),
next_order as (
    select coalesce(max(sort_order), -1) + 1 as first_order
    from public.categories
)
insert into public.categories (name, description, active, sort_order)
select catalogs.name, '', true, next_order.first_order + catalogs.display_order - 1
from catalogs
cross join next_order
where not exists (
    select 1
    from public.categories existing
    where lower(trim(existing.name)) = lower(trim(catalogs.name))
)
order by catalogs.display_order;