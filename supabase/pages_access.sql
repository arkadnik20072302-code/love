create table if not exists public.wishlist_members (
  user_id uuid primary key references auth.users (id) on delete cascade,
  created_at timestamptz not null default now()
);

alter table public.wishlist_members enable row level security;
revoke all on public.wishlist_members from public, anon, authenticated;
grant all on public.wishlist_members to service_role;

create or replace function public.is_wishlist_member()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.wishlist_members
    where user_id = (select auth.uid())
  );
$$;

revoke all on function public.is_wishlist_member() from public, anon;
grant execute on function public.is_wishlist_member() to anon, authenticated;

revoke all on public.settings, public.wishes from anon, authenticated;
grant select, update on public.settings to authenticated;
grant select, insert, update, delete on public.wishes to authenticated;

drop policy if exists wishlist_member_access on public.settings;
drop policy if exists wishlist_member_gate on public.settings;
create policy wishlist_member_access on public.settings
  for all to authenticated
  using (true)
  with check (true);
create policy wishlist_member_gate on public.settings
  as restrictive for all to authenticated
  using (public.is_wishlist_member())
  with check (public.is_wishlist_member());

drop policy if exists wishlist_member_access on public.wishes;
drop policy if exists wishlist_member_gate on public.wishes;
create policy wishlist_member_access on public.wishes
  for all to authenticated
  using (true)
  with check (true);
create policy wishlist_member_gate on public.wishes
  as restrictive for all to authenticated
  using (public.is_wishlist_member())
  with check (public.is_wishlist_member());
