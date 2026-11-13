create table if not exists public.memories (
  id text primary key,
  memory_date date not null,
  photo_path text not null,
  thought_her text not null default '' check (char_length(thought_her) <= 2000),
  thought_him text not null default '' check (char_length(thought_him) <= 2000),
  story_title text not null default '' check (char_length(story_title) <= 120),
  story_text text not null default '' check (char_length(story_text) <= 8000),
  created_at timestamptz not null default now()
);

alter table public.memories
  add column if not exists story_title text not null default ''
    check (char_length(story_title) <= 120);
alter table public.memories
  add column if not exists story_text text not null default ''
    check (char_length(story_text) <= 8000);

create index if not exists memories_memory_date_created_at_idx
  on public.memories (memory_date desc, created_at desc);

create table if not exists public.stories (
  id text primary key,
  story_date date not null,
  title text not null check (char_length(title) between 1 and 120),
  story_text text not null check (char_length(story_text) between 1 and 8000),
  photo_path text,
  created_at timestamptz not null default now()
);

create index if not exists stories_story_date_created_at_idx
  on public.stories (story_date desc, created_at desc);

insert into public.stories (id, story_date, title, story_text, photo_path, created_at)
select
  'legacy-' || id,
  memory_date,
  coalesce(nullif(trim(story_title), ''), 'История одного момента'),
  coalesce(nullif(trim(story_text), ''), 'Эта фотография тоже хранит свою историю.'),
  photo_path,
  created_at
from public.memories
where trim(story_title) <> '' or trim(story_text) <> ''
on conflict (id) do nothing;

insert into storage.buckets (
  id,
  name,
  public,
  file_size_limit,
  allowed_mime_types
)
values (
  'memories',
  'memories',
  false,
  3145728,
  array['image/jpeg', 'image/png', 'image/webp']
)
on conflict (id) do update
set public = false,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

alter table public.memories enable row level security;
alter table public.stories enable row level security;
revoke all on public.memories from anon, authenticated;
revoke all on public.stories from anon, authenticated;
grant select, insert, update, delete on public.memories to authenticated;
grant select, insert, update, delete on public.stories to authenticated;

drop policy if exists wishlist_memories_member_access on public.memories;
drop policy if exists wishlist_memories_member_gate on public.memories;
create policy wishlist_memories_member_access on public.memories
  for all to authenticated
  using (true)
  with check (true);
create policy wishlist_memories_member_gate on public.memories
  as restrictive for all to authenticated
  using (public.is_wishlist_member())
  with check (public.is_wishlist_member());

drop policy if exists wishlist_stories_member_access on public.stories;
drop policy if exists wishlist_stories_member_gate on public.stories;
create policy wishlist_stories_member_access on public.stories
  for all to authenticated
  using (true)
  with check (true);
create policy wishlist_stories_member_gate on public.stories
  as restrictive for all to authenticated
  using (public.is_wishlist_member())
  with check (public.is_wishlist_member());

drop policy if exists wishlist_memories_member_access on storage.objects;
drop policy if exists wishlist_memories_public_gate on storage.objects;
create policy wishlist_memories_member_access on storage.objects
  for all to authenticated
  using (bucket_id = 'memories')
  with check (bucket_id = 'memories');
create policy wishlist_memories_public_gate on storage.objects
  as restrictive for all to public
  using (bucket_id <> 'memories' or public.is_wishlist_member())
  with check (bucket_id <> 'memories' or public.is_wishlist_member());
