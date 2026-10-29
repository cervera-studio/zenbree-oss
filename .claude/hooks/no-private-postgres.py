"""PreToolUse hook: refuse a Bash command that starts a private Postgres container.

Agents kept starting their own (zb-lead-343-db, pg26212, zenbree-369-db, ...) next to the shared one,
and each left a volume of up to 1 GB in the Docker VM. harness/testdb.sh is not blocked: this hook sees
only the agent's command, not the commands inside the script."""

import json
import re
import sys

DOCKER_RUN = re.compile(r'\bdocker\s+(container\s+)?(run|create)\b')
POSTGRES_IMAGE = re.compile(r'(^|[\s/\'"])postgres([:@]\S*)?($|[\s\'"])')

command = json.load(sys.stdin).get('tool_input', {}).get('command', '').replace('\\\n', ' ')
for part in re.split(r'[;&|\n]+', command):
    if DOCKER_RUN.search(part) and POSTGRES_IMAGE.search(part):
        print(
            'Do not start your own Postgres container: each one leaves a volume in the shared Docker VM. '
            'For SQL tests, run eval "$(./harness/testdb.sh)" (one database server for all worktrees, a '
            'database of its own per test). For the whole app, use bin/stack in the verify skill.',
            file=sys.stderr,
        )
        sys.exit(2)
