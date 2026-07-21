    create extension if not exists "uuid-ossp";

    create table if not exists public.users (
    id uuid primary key default uuid_generate_v4(),
    email text unique not null,
    full_name text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
    );

    create or replace function public.set_updated_at()
    returns trigger as $$
    begin
    new.updated_at = now();
    return new;
    end;
    $$ language plpgsql;

    create table if not exists public.resumes (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid not null references public.users(id) on delete cascade,
    file_name text not null,
    storage_path text not null,
    extracted_text text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
    );

    create table if not exists public.searches (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid not null references public.users(id) on delete cascade,
    query text not null,
    location text,
    created_at timestamptz not null default now()
    );

    create table if not exists public.jobs (
    id uuid primary key default uuid_generate_v4(),
    source text not null,
    external_id text not null,
    title text not null,
    company text,
    location text,
    description text,
    url text,
    remote boolean default false,
    created_at timestamptz not null default now(),
    unique(source, external_id)
    );

    create table if not exists public.saved_jobs (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid not null references public.users(id) on delete cascade,
    job_id uuid not null references public.jobs(id) on delete cascade,
    created_at timestamptz not null default now(),
    unique(user_id, job_id)
    );

    drop trigger if exists users_set_updated_at on public.users;

    create trigger users_set_updated_at
    before update on public.users
    for each row execute function public.set_updated_at();

    drop trigger if exists resumes_set_updated_at on public.resumes;

    create trigger resumes_set_updated_at
    before update on public.resumes
    for each row execute function public.set_updated_at();

    create index if not exists resumes_user_id_idx on public.resumes(user_id);
    create index if not exists searches_user_id_idx on public.searches(user_id);
    create index if not exists saved_jobs_user_id_idx on public.saved_jobs(user_id);
    create index if not exists saved_jobs_job_id_idx on public.saved_jobs(job_id);
    create index if not exists jobs_source_idx on public.jobs(source);
    create index if not exists jobs_title_idx on public.jobs(title);
    create index if not exists jobs_location_idx on public.jobs(location);
