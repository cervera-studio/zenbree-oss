"""PreToolUse hook: refuse a foreground Bash command that waits: a loop that sleeps 2 s or more, a sleep of
10 s or more, `tail -f`, `docker ... logs -f`, `journalctl -f`, `watch`, or a `gh ... --watch`.

Agents started a gate in the background with their own `&`, then polled its log: `until grep -q "^exit "
x.out; do sleep 10; done` blocked one for 597 s. Such loops cost 2.7 h in four days of sessions (#444 study).
Claude Code already sends a notice when a run_in_background command ends, so the same command in the
background is allowed: it does not block the agent. A readiness poll that sleeps 1 s or less passes. A loop
that sleeps 2 s or more between API calls is refused too: run it in the background.
It runs on the system python3 (3.9 on macOS), so it uses no newer syntax.
ponytail: a wait inside a script, a heredoc fed to a shell, backticks or another language (`time.sleep` in
python3 -c) is not seen, nor are several short sleeps that add up, and a command over 50,000 characters
is not checked."""

import json
from pathlib import Path
import re
import shlex
import sys

# python3 -I leaves the script's directory off sys.path.
sys.path.append(str(Path(__file__).resolve().parent))
from shell_line import without_comments  # noqa: E402

OPENERS = {'do', 'then', 'else', 'elif', 'if', '{', '!', 'time'}
PREFIXES = {'env', 'nohup', 'nice', 'command', 'exec', 'sudo', 'timeout'}
TAKES_VALUE = {'-n', '-s', '-k', '-u', '-g'}  # options of those prefixes whose value is a separate word
SHELLS = {'bash', 'sh', 'zsh'}
UNITS = {'': 1, 's': 1, 'm': 60, 'h': 3600, 'd': 86400}
DURATION = re.compile(r'(\d+(?:\.\d*)?|\.\d+)([smhd]?)')
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n.*?\n\s*\2(?=\n|$)", re.S)
FOLLOW = re.compile(r'-\w*[fF]\w*|--follow(=\S*)?')


def seconds(arg):
    """The length of `sleep arg`, or infinity when it is not a literal, such as `$N`."""
    match = DURATION.fullmatch(arg)
    return float(match[1]) * UNITS[match[2]] if match else float('inf')


def is_separator(word):
    """shlex joins adjacent punctuation, as in `);` or `&)`: each such run separates commands."""
    return set(word) <= set(';&|()\n')


def tokens(command):
    lexer = shlex.shlex(without_comments(HEREDOC.sub(r'\3', command)), posix=True, punctuation_chars=';&|()')
    lexer.whitespace = ' \t\r'  # a newline separates commands, as in the shell
    lexer.commenters = ''  # shlex starts a comment at any `#`, the shell only at the start of a word
    lexer.wordchars += '~-./*?$=:%@+,{}[]!^#'
    try:
        return list(lexer)
    except ValueError:  # an unclosed quote: the shell refuses the command anyway
        return []


def command_after_prefix(name, args):
    """The command that a prefix such as `nice -n 10` or `timeout -s KILL 600` runs, as its words."""
    rest = list(args)
    while rest and (rest[0].startswith('-') or re.fullmatch(r'\w+=.*', rest[0])):
        rest = rest[2:] if rest[0] in TAKES_VALUE else rest[1:]
    if name == 'timeout' and rest and DURATION.fullmatch(rest[0]):
        rest = rest[1:]
    return rest


def waits(command):
    """What the command waits on, or None. Words in quotes and heredoc bodies are data, not commands."""
    words = tokens(command)
    loops = 0
    at_start = True
    for i, word in enumerate(words):
        if is_separator(word):
            at_start = True
            continue
        time_option = word == '-p' and words[i - 1 : i] == ['time']  # the one option of the `time` keyword
        if not at_start or word in OPENERS or time_option or re.fullmatch(r'\w+=.*', word):
            continue
        at_start = False
        name = word.rsplit('/', 1)[-1]
        args = []
        for arg in words[i + 1 :]:
            if is_separator(arg):
                break
            args.append(arg)
        shell_flag = next((a for a in args[:-1] if re.fullmatch(r'-\w*c\w*', a)), None)
        if name in {'while', 'until'}:
            loops += 1
            at_start = True
        elif name == 'for':
            loops += 1
        elif name == 'done':
            loops = max(loops - 1, 0)
        elif name in PREFIXES:
            found = waits(shlex.join(command_after_prefix(name, args)))
            if found:
                return found
        elif name in SHELLS and shell_flag:
            found = waits(args[args.index(shell_flag) + 1])
            if found:
                return found
        elif name == 'sleep':
            length = sum(seconds(a) for a in args if a != '--')  # sleep adds its arguments
            if length >= 10:
                return f'sleep {length:g} s'
            if loops and length >= 2:
                return 'a loop that sleeps'
        elif name == 'tail' and any(FOLLOW.fullmatch(a) for a in args):
            return 'tail -f'
        elif name == 'docker' and 'logs' in args and any(FOLLOW.fullmatch(a) for a in args[args.index('logs') :]):
            return 'docker logs -f'
        elif name == 'journalctl' and any(FOLLOW.fullmatch(a) for a in args):
            return 'journalctl -f'
        elif name == 'watch':
            return 'watch'
        elif name == 'gh' and ('--watch' in args or args[:2] == ['run', 'watch']):
            return 'gh --watch'
    return None


def main():
    try:
        tool_input = json.load(sys.stdin).get('tool_input') or {}
        command = tool_input.get('command')
    except (ValueError, AttributeError):
        return 0  # not a Bash payload from Claude Code: nothing to check
    if tool_input.get('run_in_background') or not isinstance(command, str) or len(command) > 50_000:
        return 0
    try:
        found = waits(command.replace('\\\n', ' '))
    except RecursionError:  # hundreds of nested prefixes: no command an agent writes
        return 0
    if not found:
        return 0
    print(
        f'Do not wait in the foreground ({found}): the call blocks you until it ends. To wait for a command you '
        'start, run it with run_in_background: true and end your turn: a notice arrives when it ends. To wait '
        'for something outside, such as a push, use the Monitor tool, or run the loop with run_in_background: '
        'true. A readiness poll may loop on a sleep of 1 s or less.',
        file=sys.stderr,
    )
    return 2


sys.exit(main())
