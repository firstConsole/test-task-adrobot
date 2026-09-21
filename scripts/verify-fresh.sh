#!/usr/bin/env bash
# What a reviewer gets from a bare clone, verified the way they will meet it: clone, copy
# the example environment, bring the stack up, ask it questions over HTTP.
#
#     scripts/verify-fresh.sh                  # clone this repository's HEAD
#     scripts/verify-fresh.sh <url-or-path>    # clone somewhere else, e.g. the GitHub remote
#     scripts/verify-fresh.sh --keep           # leave the stack up to poke at it
#
# Three decisions worth reading before the code.
#
# **It clones rather than using the working tree.** Uncommitted work is exactly what a
# reviewer will not have, so a run that tested it would answer the wrong question. The run
# says so out loud when the tree is dirty.
#
# **It runs under its own compose project name**, and that is the one place it departs from
# the literal commands in the README. Both stacks are called `adrobot`, so a teardown here
# would take the `pgdata` volume of the developer's own stack with it. The ports are the
# README's, though — they are part of what is being verified — so the run refuses to start
# while something else holds them.
#
# **It only asks for what works without a tracker.** `.env.example` points at
# `tracker.invalid` by design, so every check below is one this service answers out of its
# own database: health, authentication, the campaign list, and the frontend's proxy.
#
# The image tags in docker-compose.yml are fixed (`adrobot-api:dev`), so this run rebuilds
# them for everyone. They are built from the committed tree, which is the better copy anyway.

set -euo pipefail

readonly PROJECT="adrobot-verify"
readonly API="http://127.0.0.1:8000"
readonly WEB="http://127.0.0.1:8080"
readonly PORTS=(8000 8080 5432)

keep=false
source_repo=""
for argument in "$@"; do
    case "$argument" in
        --keep) keep=true ;;
        -h|--help) sed -n '2,8p' "$0" | cut -c3- ; exit 0 ;;
        -*) echo "unknown option: $argument" >&2; exit 2 ;;
        *) source_repo="$argument" ;;
    esac
done

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${source_repo:=$repository_root}"

failures=0
workspace=""

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad() { printf '  \033[31m✗\033[0m %s\n' "$*"; failures=$((failures + 1)); }

# Every check is one line: what was asked, what was expected, what came back. A failing one
# does not stop the run — the second question is usually the one that explains the first.
expect() {
    local what="$1" expected="$2" actual="$3"
    if [[ "$actual" == *"$expected"* ]]; then ok "$what"; else bad "$what — ожидалось «$expected», пришло «$actual»"; fi
}

status_of() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$@"; }

cleanup() {
    local code=$?
    if [[ -n "$workspace" && -d "$workspace" ]]; then
        if [[ "$keep" == true ]]; then
            say "Стек оставлен: $workspace"
            echo "  снести — docker compose -p $PROJECT -f $workspace/docker-compose.yml down -v"
        else
            say "Убираю за собой"
            (cd "$workspace" && docker compose -p "$PROJECT" down -v --remove-orphans >/dev/null 2>&1) || true
            rm -rf "$workspace"
        fi
    fi
    exit "$code"
}
trap cleanup EXIT

say "Проверки окружения"
command -v git >/dev/null || { bad "git не найден"; exit 1; }
command -v curl >/dev/null || { bad "curl не найден"; exit 1; }
docker compose version >/dev/null 2>&1 || { bad "нет docker compose v2"; exit 1; }
ok "git, curl и docker compose на месте"

for port in "${PORTS[@]}"; do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
        exec 3>&- 3<&-
        bad "порт $port занят — остановите свой стек (make down) и повторите"
        exit 1
    fi
done
ok "порты ${PORTS[*]} свободны"

if [[ "$source_repo" == "$repository_root" ]] && ! git -C "$repository_root" diff --quiet HEAD 2>/dev/null; then
    printf '  \033[33m!\033[0m в рабочем дереве есть незакоммиченное — проверяется HEAD, а не оно\n'
fi

say "Клонирую $source_repo"
workspace="$(mktemp -d)"
git clone --quiet --depth 1 "$source_repo" "$workspace/test-task-adrobot"
cd "$workspace/test-task-adrobot"
ok "клон: $(git rev-parse --short HEAD) $(git log -1 --pretty=%s)"

say "Команды из README"
cp .env.example .env
ok "cp .env.example .env"
docker compose -p "$PROJECT" up -d --wait --build
ok "docker compose up -d --wait --build"

token="$(grep '^ADROBOT_ACCESS_TOKEN=' .env | cut -d= -f2-)"

say "API"
expect "GET /healthz отвечает 200"        "200"          "$(status_of "$API/healthz")"
expect "тело /healthz"                    '"status":"ok"' "$(curl -s "$API/healthz")"
expect "GET /readyz отвечает 200"         "200"          "$(status_of "$API/readyz")"
expect "/readyz не падает без трекера"    '"status":"ok"' "$(curl -s "$API/readyz")"
expect "список кампаний без токена — 401" "401"          "$(status_of "$API/api/v1/campaigns")"
expect "список кампаний с токеном — 200"  "200"          "$(status_of -H "Authorization: Bearer $token" "$API/api/v1/campaigns")"
expect "и отвечает из своей базы"         '"campaigns"'  "$(curl -s -H "Authorization: Bearer $token" "$API/api/v1/campaigns")"
expect "/docs открыт в dev"               "200"          "$(status_of "$API/docs")"

say "Миграции"
applied="$(docker compose -p "$PROJECT" exec -T db psql -qtAX -U adrobot -d adrobot \
    -c 'select version_num from alembic_version' 2>/dev/null | tr -d '[:space:]')"
head_revision="$(ls backend/alembic/versions | sed 's/_.*//' | sort | tail -1)"
expect "alembic доведён до $head_revision" "$head_revision" "$applied"
expect "таблицы созданы" "8" "$(docker compose -p "$PROJECT" exec -T db psql -qtAX -U adrobot -d adrobot \
    -c "select count(*) from information_schema.tables where table_schema='public' and table_name <> 'alembic_version'" \
    2>/dev/null | tr -d '[:space:]')"

say "Интерфейс"
expect "web отдаёт страницу"              "200"       "$(status_of "$WEB/")"
expect "это собранный SPA"                'id="root"' "$(curl -s "$WEB/")"
expect "неизвестный путь — тоже SPA"      "200"       "$(status_of "$WEB/campaigns/new")"
expect "nginx проксирует и держит токен"  "200"       "$(status_of "$WEB/api/v1/campaigns")"

say "Итог"
if ((failures == 0)); then
    printf '  \033[32mвсё сошлось\033[0m: чистый клон поднимается четырьмя командами и отвечает\n'
else
    printf '  \033[31mнеудач: %d\033[0m\n' "$failures"
fi
exit $((failures > 0))
