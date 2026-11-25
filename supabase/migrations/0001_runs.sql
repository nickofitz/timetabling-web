-- Historial de corridas de timetabling-web. Ejecutar una vez en el SQL Editor de Supabase.

create table if not exists public.runs (
    id             text primary key check (id ~ '^[0-9a-f]{12}$'),
    created_at     timestamptz not null default now(),
    source         text not null,
    solver         text not null check (solver in ('highs', 'cp-sat')),
    time_limit     double precision not null,
    gamma          integer not null check (gamma >= 0),
    status         text not null,
    sections       integer not null,
    alternatives   integer not null,
    objective      integer,
    total          integer,
    best_bound     double precision,
    gap            double precision,
    windows        integer,
    audit          text,
    audit_findings jsonb not null default '[]',
    seconds        double precision not null,
    solve_seconds  double precision,
    precheck       jsonb not null,
    schedule_html  text,
    schedule_csv   text
);

create index if not exists runs_created_at on public.runs (created_at desc);

-- RLS sin políticas: las claves públicas (anon, authenticated) no leen ni escriben nada.
alter table public.runs enable row level security;

-- Desde el 30 de mayo de 2026 las tablas nuevas no quedan expuestas a la Data API sin un
-- GRANT explícito. Solo el servidor, con la clave secreta (rol service_role), las usa.
grant select, insert on public.runs to service_role;

-- Bucket privado para los ZIP subidos.
insert into storage.buckets (id, name, public)
values ('instances', 'instances', false)
on conflict (id) do nothing;
