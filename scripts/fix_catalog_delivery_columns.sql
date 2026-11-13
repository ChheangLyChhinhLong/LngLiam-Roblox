alter table public.categories add column if not exists delivery_message text not null default '';
alter table public.categories add column if not exists delivery_media_url text;

notify pgrst, 'reload schema';
