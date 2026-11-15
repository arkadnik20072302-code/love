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

alter table public.music_tracks enable row level security;
revoke all on public.music_tracks from anon, authenticated;
grant select, insert, delete on public.music_tracks to authenticated;
grant all on public.music_tracks to service_role;

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
