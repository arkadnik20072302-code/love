create extension if not exists pg_net with schema extensions;

create table if not exists public.telegram_sessions (
  telegram_user_id text primary key check (telegram_user_id ~ '^[0-9]+$'),
  pending_action text not null check (pending_action in ('wish', 'reminder')),
  updated_at timestamptz not null default now()
);

create table if not exists public.telegram_updates (
  update_id bigint primary key check (update_id >= 0),
  claimed_at timestamptz not null default now(),
  processed_at timestamptz
);

create index if not exists telegram_updates_processed_at_idx
  on public.telegram_updates (processed_at)
  where processed_at is not null;

create table if not exists public.telegram_dispatch_lock (
  id smallint primary key check (id = 1),
  locked_until timestamptz
);

insert into public.telegram_dispatch_lock (id)
values (1)
on conflict (id) do nothing;

alter table public.telegram_sessions enable row level security;
alter table public.telegram_updates enable row level security;
alter table public.telegram_dispatch_lock enable row level security;

revoke all on public.telegram_sessions, public.telegram_updates,
  public.telegram_dispatch_lock from anon, authenticated;
grant all on public.telegram_sessions, public.telegram_updates,
  public.telegram_dispatch_lock to service_role;

create or replace function public.try_claim_telegram_dispatch(lock_seconds integer)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  claimed integer;
begin
  if lock_seconds < 30 or lock_seconds > 600 then
    raise exception 'lock_seconds must be between 30 and 600';
  end if;

  update public.telegram_dispatch_lock
  set locked_until = pg_catalog.now() +
    pg_catalog.make_interval(secs => lock_seconds)
  where id = 1
    and (locked_until is null or locked_until < pg_catalog.now());

  get diagnostics claimed = row_count;
  return claimed = 1;
end;
$$;

revoke all on function public.try_claim_telegram_dispatch(integer)
  from public, anon, authenticated;
grant execute on function public.try_claim_telegram_dispatch(integer)
  to service_role;

create or replace function public.claim_telegram_update(p_update_id bigint)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  claimed integer;
begin
  if p_update_id < 0 then
    raise exception 'update ID must be non-negative';
  end if;

  delete from public.telegram_updates
  where processed_at < pg_catalog.now() - interval '30 days';

  insert into public.telegram_updates (update_id)
  values (p_update_id)
  on conflict (update_id) do nothing;

  get diagnostics claimed = row_count;
  if claimed = 1 then
    return true;
  end if;

  update public.telegram_updates
  set claimed_at = pg_catalog.now()
  where update_id = p_update_id
    and processed_at is null
    and claimed_at < pg_catalog.now() - interval '5 minutes';

  get diagnostics claimed = row_count;
  return claimed = 1;
end;
$$;

revoke all on function public.claim_telegram_update(bigint)
  from public, anon, authenticated;
grant execute on function public.claim_telegram_update(bigint)
  to service_role;

create or replace function public.release_telegram_update(p_update_id bigint)
returns void
language sql
security definer
set search_path = ''
as $$
  delete from public.telegram_updates
  where update_id = p_update_id and processed_at is null;
$$;

revoke all on function public.release_telegram_update(bigint)
  from public, anon, authenticated;
grant execute on function public.release_telegram_update(bigint)
  to service_role;

create or replace function public.notify_telegram_wish_completed()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.status = 'done' and old.status is distinct from 'done' then
    perform net.http_post(
      url := (
        select decrypted_secret
        from vault.decrypted_secrets
        where name = 'wishlist_telegram_function_url'
        limit 1
      ),
      headers := pg_catalog.jsonb_build_object(
        'content-type', 'application/json',
        'x-telegram-internal-secret', (
          select decrypted_secret
          from vault.decrypted_secrets
          where name = 'wishlist_telegram_internal_secret'
          limit 1
        )
      ),
      body := pg_catalog.jsonb_build_object(
        'action', 'notify_wish_completed',
        'title', new.title
      )
    );
  end if;
  return new;
end;
$$;

revoke all on function public.notify_telegram_wish_completed()
  from public, anon, authenticated;

drop trigger if exists wishes_notify_telegram_completed on public.wishes;
create trigger wishes_notify_telegram_completed
after update of status on public.wishes
for each row
execute function public.notify_telegram_wish_completed();
