#!/bin/sh
# Process a sub-agent's queue inside its container, one task at a time.
# Claims each task before calling Pi and blocks unfinished work,
# so interrupted or failed runs do not silently disappear or get retried.
set -eu

kanban=/agent/kanban

# Point main at the newest attempt, as seen from its /data mount.
session_line() {
    session=$(ls -t /agent/home/.pi/task-sessions/"$1"/*.jsonl 2>/dev/null | head -n 1)
    if [ -n "$session" ]; then
        printf '\nSession: /data/agents/%s/home%s\n' "$AGENT_NAME" "${session#/agent/home}"
    else
        printf '\nSession: none saved; Pi exited before it started.\n'
    fi
}

# A restart must not silently repeat a task's side effects.
for task in "$kanban"/in-progress/*.md; do
    [ -f "$task" ] || continue
    name=${task##*/}
    printf '\n## Result\nInterrupted by a container restart. Review before retrying.\n' >> "$task"
    session_line "${name%.md}" >> "$task"
    mv "$task" "$kanban/blocked/$name"
done

# Timestamped filenames put older tasks first. New arrivals run on the next pass.
for queued in "$kanban"/todo/*.md; do
    [ -f "$queued" ] || continue
    name=${queued##*/}
    task=$kanban/in-progress/$name
    mv "$queued" "$task" 2>/dev/null || continue

    printf '=== %s\n' "$name"
    # Keep a fresh session per attempt, grouped by task in the persistent home.
    sessions=/agent/home/.pi/task-sessions/${name%.md}
    mkdir -p "$sessions"
    agent-run --print --session-dir "$sessions" < "$task" || true

    # File-only agents save a completed copy; agents with a shell can move it.
    if [ -f "$task" ]; then
        if [ -f "$kanban/done/$name" ] || [ -f "$kanban/blocked/$name" ]; then
            rm "$task"
        else
            printf '\n## Result\nAgent exited without filing the task.\n' >> "$task"
            mv "$task" "$kanban/blocked/$name"
        fi
    fi
    for filed in "$kanban/done/$name" "$kanban/blocked/$name"; do
        [ ! -f "$filed" ] || session_line "${name%.md}" >> "$filed"
    done
done
