#!/bin/sh
# Starts AgentLab on Railway (docs/deployment-railway.md). railway.json names this script as `deploy.startCommand`, which
# replaces the image's ENTRYPOINT, so it does what the ENTRYPOINT does and three things more:
#
#   1. It listens on the port Railway injects as $PORT (8080 when there is none), on every interface.
#   2. It uses the configuration for one Railway service, /etc/agentlab/railway.yaml (docker/agentlab.railway.yaml): jobs
#      run in the same process, because a Railway volume belongs to a single service.
#   3. A Railway volume is mounted owned by root, which the image's own user (uid 10001) cannot write to. Railway's
#      remedy is the service variable RAILWAY_RUN_UID=0, which starts the container as root. Here that is only the
#      start: the script gives the data directory to the unprivileged user and runs AgentLab as that user, with no way
#      back to root (`no_new_privs`), so a root process never serves a request. Started as the unprivileged user it only
#      checks that the data directory can be written, and says what to do when it cannot.
#
# Arguments are AgentLab arguments. With none (or just `serve`) the script runs `serve`; anything else is passed on, so
# `agentlab-railway-start doctor` runs `agentlab doctor` with the same configuration and the same user.
#
# AGENTLAB_DATA_DIR (default /data) is where the volume is mounted. AGENTLAB_RUN_USER (default agentlab) is the user to
# run as; the image has exactly one, so this exists for the tests, which cannot assume it.
set -eu

DATA_DIR="${AGENTLAB_DATA_DIR:-/data}"
RUN_USER="${AGENTLAB_RUN_USER:-agentlab}"
CONFIG="/etc/agentlab/railway.yaml"

if [ "$#" -eq 0 ] || { [ "$#" -eq 1 ] && [ "$1" = "serve" ]; }; then
    set -- serve --host 0.0.0.0 --port "${PORT:-8080}"
fi
set -- agentlab --config "$CONFIG" "$@"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    # Only what the unprivileged user does not own already, so a volume with many files starts as fast as an empty one.
    # `-h` changes a symbolic link itself and never what it points at.
    find "$DATA_DIR" -xdev ! -user "$RUN_USER" -exec chown -h "$RUN_USER" {} +
    HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
    export HOME
    exec setpriv --reuid="$(id -u "$RUN_USER")" --regid="$(id -g "$RUN_USER")" --clear-groups --no-new-privs -- "$@"
fi

if [ ! -w "$DATA_DIR" ]; then
    echo "agentlab: $DATA_DIR cannot be written by user $(id -u). A Railway volume is mounted owned by root: set the" >&2
    echo "service variable RAILWAY_RUN_UID=0 (this script then drops to the unprivileged user itself), or run without" >&2
    echo "a volume. See docs/deployment-railway.md." >&2
    exit 1
fi
exec "$@"
