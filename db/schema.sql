-- Gajab local Postgres schema (Option A: Supabase -> Windows box).
--
-- Derived from the LIVE Supabase tables by sampling real values, because the
-- in-repo migrations are stale (supabase/migrations/price_mappings.sql predates
-- the *_dinov2 / *_clip / *_match_tag / *_reliable columns, and products /
-- product_duplicates have no schema file at all).
--
-- Types worth noting:
--   * price fields are TEXT ("₹292"), not numeric - keep them text so the
--     application round-trips exactly what the marketplace showed.
--   * price_mappings.search_error is TEXT holding a JSON string (the old
--     migration says TEXT; the live rows confirm it is a stringified object,
--     NOT jsonb).
--   * monitoring_runs.details IS jsonb.
--   * *_match_score / *_dinov2 / *_clip are numeric: live rows hold both ints
--     (80) and floats (0.8923).
--
-- Apply with:
--   psql -U postgres -h 127.0.0.1 -d gajab -v ON_ERROR_STOP=1 -f schema.sql

create table if not exists products (
    id          text primary key,
    name        text,
    price       text,
    mrp_price   text,
    image_url   text,
    url         text,
    category    text,
    brand       text,
    seller      text,
    created_at  timestamptz default now()
);
create index if not exists products_created_at_idx on products (created_at desc);
create index if not exists products_name_idx       on products (name);
create index if not exists products_url_idx        on products (url);

create table if not exists price_mappings (
    id                    uuid primary key default gen_random_uuid(),
    gajab_product_id      text unique not null,
    gajab_title           text,
    gajab_image_url       text,
    gajab_price           text,
    gajab_url             text,
    amazon_url            text,
    amazon_price          text,
    amazon_match_score    numeric,
    amazon_dinov2         numeric,
    amazon_clip           numeric,
    amazon_match_tag      text,
    amazon_unavailable    boolean default false,
    amazon_reliable       boolean default false,
    flipkart_url          text,
    flipkart_price        text,
    flipkart_match_score  numeric,
    flipkart_dinov2       numeric,
    flipkart_clip         numeric,
    flipkart_match_tag    text,
    flipkart_unavailable  boolean default false,
    flipkart_reliable     boolean default false,
    meesho_url            text,
    meesho_price          text,
    meesho_match_score    numeric,
    meesho_dinov2         numeric,
    meesho_clip           numeric,
    meesho_match_tag      text,
    meesho_unavailable    boolean default false,
    meesho_reliable       boolean default false,
    search_error          text,
    last_checked          timestamptz default now(),
    -- Per-platform "when was this price last re-fetched from the stored link".
    -- The prices were captured at mapping time and drift; the Price Refetch job
    -- re-scrapes the stored URL and stamps the platform it refreshed.
    amazon_price_checked_at   timestamptz,
    flipkart_price_checked_at timestamptz,
    meesho_price_checked_at   timestamptz,
    created_at            timestamptz default now()
);
create index if not exists price_mappings_gajab_id_idx   on price_mappings (gajab_product_id);
create index if not exists price_mappings_last_checked_idx on price_mappings (last_checked desc);
-- the refetch job walks oldest-first
create index if not exists price_mappings_last_checked_asc_idx on price_mappings (last_checked asc nulls first);

create table if not exists product_duplicates (
    id            uuid primary key default gen_random_uuid(),
    product_id    text not null,
    duplicate_of  text not null,
    dinov2_score  numeric,
    created_at    timestamptz default now()
);
create index if not exists product_duplicates_product_id_idx on product_duplicates (product_id);
create index if not exists product_duplicates_dup_of_idx     on product_duplicates (duplicate_of);

create table if not exists monitoring_runs (
    id           uuid primary key default gen_random_uuid(),
    run_at       timestamptz not null default now(),
    run_id       text,
    page         text,
    metric       text,
    value        double precision,
    status       text check (status in ('pass', 'fail', 'degraded')),
    step_failed  text,
    duration_ms  integer,
    details      jsonb,
    issue_type   text,
    created_at   timestamptz default now()
);
create index if not exists monitoring_runs_run_at_idx on monitoring_runs (run_at desc);
create index if not exists monitoring_runs_run_id_idx on monitoring_runs (run_id);
create index if not exists monitoring_runs_page_idx   on monitoring_runs (page);
create index if not exists monitoring_runs_status_idx on monitoring_runs (status);

-- PostgREST switches into these roles per the JWT's `role` claim.
grant usage on schema public to anon, service_role;
grant all on all tables in schema public to anon, service_role;
grant all on all sequences in schema public to anon, service_role;
