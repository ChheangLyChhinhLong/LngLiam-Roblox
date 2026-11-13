alter table public.products add column if not exists delivery_message text not null default '';
alter table public.products add column if not exists delivery_media_url text;
alter table public.products add column if not exists delivery_image_url text;
alter table public.products add column if not exists delivery_video_url text;

notify pgrst, 'reload schema';
