create table if not exists public.settings (
  id smallint primary key check (id = 1),
  together_since date,
  demob_date date
);

insert into public.settings (id)
values (1)
on conflict (id) do nothing;

create table if not exists public.wishes (
  id text primary key default replace(gen_random_uuid()::text, '-', ''),
  title text not null check (char_length(title) between 1 and 120),
  category text not null default 'Разное',
  status text not null default 'planned'
    check (status in ('planned', 'done')),
  created_at date not null default current_date,
  planned_for date,
  planned_time time,
  completed_at date,
  completed_time time,
  photo text not null default ''
);

alter table public.wishes
  add column if not exists planned_time time;
alter table public.wishes
  add column if not exists completed_time time;

create table if not exists public.reminders (
  id text primary key,
  title text not null check (char_length(title) between 1 and 240),
  remind_at timestamptz not null,
  created_by text not null,
  sent boolean not null default false,
  sent_to text not null default ''
);

create table if not exists public.memories (
  id text primary key,
  memory_date date not null,
  photo_path text not null,
  thumbnail_path text,
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
alter table public.memories
  add column if not exists thumbnail_path text;

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

create table if not exists public.music_tracks (
  id text primary key,
  title text not null check (char_length(title) between 1 and 120),
  yandex_url text not null,
  track_id text not null check (track_id ~ '^[0-9]+$'),
  album_id text not null default '' check (album_id = '' or album_id ~ '^[0-9]+$'),
  created_at timestamptz not null default now()
);

create index if not exists music_tracks_created_at_idx
  on public.music_tracks (created_at asc, id asc);

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

alter table public.settings enable row level security;
alter table public.wishes enable row level security;
alter table public.reminders enable row level security;
alter table public.memories enable row level security;
alter table public.stories enable row level security;
alter table public.music_tracks enable row level security;

revoke all on public.settings, public.wishes, public.reminders, public.memories, public.stories, public.music_tracks from anon, authenticated;
grant all on public.settings, public.wishes, public.reminders, public.memories, public.stories, public.music_tracks to service_role;
grant select, insert, delete on public.music_tracks to authenticated;

drop policy if exists wishlist_music_tracks_member_access on public.music_tracks;
drop policy if exists wishlist_music_tracks_member_gate on public.music_tracks;
create policy wishlist_music_tracks_member_access on public.music_tracks
  for all to authenticated
  using (true)
  with check (true);
create policy wishlist_music_tracks_member_gate on public.music_tracks
  as restrictive for all to authenticated
  using (public.is_wishlist_member())
  with check (public.is_wishlist_member());
