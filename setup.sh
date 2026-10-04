#!/bin/bash
set -e

MODE="${1:-}"

log_info() { echo -e "\033[0;32m[INFO]\033[0m $1"; }
log_warn() { echo -e "\033[1;33m[WARN]\033[0m $1"; }
log_error() { echo -e "\033[0;31m[ERROR]\033[0m $1"; }

# A signing secret for local development. Three sources are tried in turn so that
# generating one adds no dependency to this repository: any Python 3, then OpenSSL, then
# the kernel's own pool. The first source that returns a value with something in it wins,
# and the value itself is written straight to the env file and never echoed to the
# terminal. A source that exits quietly with nothing to say is treated as unusable rather
# than accepted, because blank whitespace would be written out as though it were a secret.
random_secret() {
    local secret

    if command -v python3 >/dev/null 2>&1; then
        if secret=$(python3 -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null) && [ -n "${secret//[[:space:]]/}" ]; then
            printf '%s\n' "$secret"
            return 0
        fi
    fi

    if command -v openssl >/dev/null 2>&1; then
        if secret=$(openssl rand -hex 32 2>/dev/null) && [ -n "${secret//[[:space:]]/}" ]; then
            printf '%s\n' "$secret"
            return 0
        fi
    fi

    if [ -r /dev/urandom ]; then
        secret=$(od -An -tx1 -N32 /dev/urandom 2>/dev/null | tr -d ' \n')
        if [ -n "${secret//[[:space:]]/}" ]; then
            printf '%s\n' "$secret"
            return 0
        fi
    fi

    return 1
}

# Whether an env file already gives the named variable a value that is not blank. A
# quoted or unquoted empty assignment counts as blank, because that is exactly what a
# template line looks like to a reader and exactly what must be replaced.
env_has_value() {
    local line value
    line=$(grep -m1 -E "^[[:space:]]*(export[[:space:]]+)?$2[[:space:]]*=" "$1") || return 1
    [ -n "$line" ] || return 1

    value=${line#*=}
    value=$(printf '%s' "$value" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/')

    [ -n "$value" ]
}

# Give a variable a value in an env file, replacing an existing assignment or appending
# one. Written through a temporary file because sed -i takes different arguments on GNU
# and on BSD, and the difference is not something a setup script should care about.
env_set_value() {
    local file="$1" name="$2" value="$3"

    if grep -qE "^[[:space:]]*(export[[:space:]]+)?$name[[:space:]]*=" "$file"; then
        local tmp
        # Named explicitly, and private to its owner, because this copy holds a signing
        # key: it must not depend on where mktemp happens to default to, and it must not
        # be world-readable for the moment it exists.
        tmp=$(mktemp "${TMPDIR:-/tmp}/wallet-backend-env.XXXXXX")
        chmod 600 "$tmp"
        sed -E "s|^[[:space:]]*(export[[:space:]]+)?$name[[:space:]]*=.*$|$name=$value|" "$file" >"$tmp"
        mv "$tmp" "$file"
    else
        printf '%s=%s\n' "$name" "$value" >>"$file"
    fi
}

# The committed example carries the two secret names with nothing after them, so a file
# copied from it is a template and not yet a working configuration. Fill them in here,
# once, and leave every later run alone: a name that already holds a value keeps it,
# which is what stops a re-run from silently invalidating the tokens of a running app and
# from quietly disagreeing with a database that was seeded under the old value.
ensure_backend_secrets() {
    local file="backend/.env"
    local generated=0
    local name secret

    for name in DJANGO_SECRET_KEY JWT_SECRET; do
        if env_has_value "$file" "$name"; then
            continue
        fi

        if ! secret=$(random_secret); then
            log_error "Could not generate $name: need python3, openssl, or /dev/urandom on PATH."
            exit 1
        fi

        if [ -z "${secret//[[:space:]]/}" ]; then
            log_error "Could not generate $name: every source returned a blank value."
            exit 1
        fi

        env_set_value "$file" "$name" "$secret"
        generated=1
    done

    if [ "$generated" -eq 1 ]; then
        log_info "Generated local development secrets in backend/.env"
    fi
}

setup_env() {
    for file in .env frontend/.env backend/.env; do
        example="${file}.example"
        if [ -f "$example" ] && [ ! -f "$file" ]; then
            cp "$example" "$file"
            log_info "Created $file"
        fi
    done

    # Runs before MongoDB, before the virtualenv, and long before anything invokes
    # Django, so the secrets are in place by the time settings are first imported.
    if [ -f "backend/.env" ]; then
        ensure_backend_secrets
    fi
}

MONGO_HOST="127.0.0.1"
MONGO_PORT="27017"

mongo_is_reachable() {
    (exec 3<>"/dev/tcp/${MONGO_HOST}/${MONGO_PORT}") >/dev/null 2>&1
}

check_mongo() {
    if mongo_is_reachable; then
        log_info "MongoDB is already reachable on ${MONGO_PORT}"
        return
    fi
    log_warn "MongoDB is not reachable; starting it"
    if [ -f /etc/mongod.conf ]; then
        mongod --config /etc/mongod.conf --fork >/dev/null 2>&1 || true
    else
        mkdir -p .mongodb
        mongod --dbpath .mongodb --bind_ip "$MONGO_HOST" --port "$MONGO_PORT" --fork --logpath .mongodb/mongod.log >/dev/null 2>&1 || true
    fi
    mongo_is_reachable || {
        log_error "MongoDB is required on ${MONGO_HOST}:${MONGO_PORT}. Start it, then run this again."
        exit 1
    }
}

setup_python() {
    if [ ! -d ".venv" ]; then
        log_info "Creating .venv with Python 3.12"
        uv venv --python 3.12 .venv
    fi
    uv --system-certs pip install --python .venv/bin/python -r backend/requirements.txt --quiet
    log_info "Python dependencies ready"
}

seed_database() {
    log_info "Seeding Wallet database"
    bun run seed
}

setup_env
check_mongo
setup_python

case "$MODE" in
    --start|--seed|"") seed_database ;;
    *) log_error "Unknown setup mode: $MODE"; exit 1 ;;
esac

log_info "Wallet setup complete"
