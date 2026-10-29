"""The commands of a Bash tool call, for the hooks that act on one command such as `gh pr ready`."""

import os
import re
import shlex

ASSIGNMENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*=')
# Words that start a command without being its program: shell keywords and built-ins, then wrappers that run
# the words after them.
OPENERS = {'do', 'then', 'else', 'elif', 'if', 'while', 'until', '{', '!', 'eval', 'builtin', 'coproc'}
WRAPPERS = {'env', 'nohup', 'nice', 'command', 'exec', 'sudo', 'timeout', 'time'}
WRAPPER_VALUE_OPTIONS = {'-n', '-s', '-k', '-u', '-g', '-a'}  # options of the wrappers whose value is a separate word
DURATION = re.compile(r'(\d+(?:\.\d*)?|\.\d+)([smhd]?)')
# The shell's operators, longest first. One with `<` or `>` is a redirection, whose next word is its target.
OPERATOR = re.compile(r'&>>|&>|<<<|<<|<&|<>|>>|>&|>\||[<>]|;;&?|;&|&&|\|\||\|&|[;&|()]')
# A backslash escape or quoted string, else a word of digits that starts a word and touches a `<` or `>`: the fd of a redirection.
FD_WORD = re.compile(r'''(\\.|"(?:\\.|[^"\\])*"|'[^']*')|(?<![^\s;&|()])\d+(?=[<>])''')


def commands(line):
    """Each command of a shell line as its words, from its program on (see `program_words`). A quoted
    "gh pr ready" in a grep or a commit message stays one word. The operator and target of a redirection such as
    `2>/dev/null` are dropped, and so is its file descriptor. A line that shlex cannot split, such as an
    unclosed quote or a heredoc with an apostrophe, gives None."""
    # Comments are cut per line before the newlines become `;`: shlex would run one to the end of the input. They
    # are cut before continuations join lines, because a `\` at the end of a comment does not continue it.
    text = without_comments(line).replace('\\\n', ' ').replace('\n', ';')
    text = FD_WORD.sub(lambda m: m.group(1) or '', text)
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ''
    found, words = [], []
    target_next = False
    try:
        for token in lexer:
            if not set(token) <= set(lexer.punctuation_chars):
                if not target_next:
                    words.append(token)
                target_next = False
                continue
            # shlex joins a run of operators such as `&&>` or `;>` into one token.
            for operator in OPERATOR.findall(token):
                if set(operator) & set('<>'):
                    target_next = True
                else:
                    found.append(words)
                    words, target_next = [], False
    except ValueError:
        return None
    found.append(words)
    return [program_words(w) for w in found]


def without_comments(command):
    """The command with each comment cut: a `#` that starts a word outside quotes, to the end of its line."""
    out, quote, i, word_start = [], None, 0, True
    while i < len(command):
        char = command[i]
        if char == '\\' and quote != "'":
            out.append(command[i : i + 2])
            # An escaped character such as the `;` of `a\;#b` is part of the word; a continuation joins lines.
            word_start = word_start and command[i + 1 : i + 2] == '\n'
            i += 2
            continue
        if quote:
            quote = None if char == quote else quote
        elif char in '\'"':
            quote = char
        elif char == '#' and word_start:
            end = command.find('\n', i)
            i = len(command) if end < 0 else end
            continue
        out.append(char)
        word_start = not quote and char in ' \t\n;&|()'
        i += 1
    return ''.join(out)


def program_words(words):
    """One command's words from the program it runs: without leading keywords, VAR=value words and wrappers
    such as `nohup` or `timeout -s KILL 30`."""
    while words:
        if words[0] in OPENERS or ASSIGNMENT.match(words[0]):
            words = words[1:]
        elif os.path.basename(words[0]) in WRAPPERS:
            words = wrapped(os.path.basename(words[0]), words[1:])
        else:
            break
    return words


def wrapped(name, args):
    """The words that a wrapper such as `nice -n 10` or `timeout -s KILL 600` runs."""
    rest = list(args)
    # `command -v gh` only names the program.
    if name == 'command' and rest[:1] in (['-v'], ['-V']):
        return []
    while rest and (rest[0].startswith('-') or ASSIGNMENT.match(rest[0])):
        rest = rest[2:] if rest[0] in WRAPPER_VALUE_OPTIONS else rest[1:]
    if name == 'timeout' and rest and DURATION.fullmatch(rest[0]):
        rest = rest[1:]
    return rest


def gh_words(words):
    """The words after `gh` in one command's words, or None for any other command. A global `-R`/`--repo` before
    the group or the subcommand moves to the end, so the group and the subcommand come first."""
    if not words or os.path.basename(words[0]) != 'gh':
        return None
    rest, repo = _without_repo(words[1:])
    group, rest = rest[:1], rest[1:]
    rest, repo = _without_repo(rest, repo)
    return group + rest + repo


def gh_command(words, group, subcommand):
    """The arguments of `gh <group> <subcommand>` in one command's words, or None for any other command."""
    rest = gh_words(words)
    if rest is None or rest[:2] != [group, subcommand]:
        return None
    return rest[2:]


def _without_repo(words, repo=()):
    """`words` without a leading `-R`/`--repo` flag, which gh takes before the group and before the subcommand."""
    repo = list(repo)
    while words and (words[0] in ('-R', '--repo') or words[0].startswith('--repo=')):
        size = 1 if '=' in words[0] else 2
        repo, words = words[:size], words[size:]
    return words, repo


def pr_target(args, value_flags=()):
    """The PR that `gh pr` arguments pick, as (its number, URL or branch, or None; the `--repo` arguments). The
    words after the flags in `value_flags` are their values, not the PR."""
    repo, target = [], None
    rest = iter(args)
    for word in rest:
        if word in ('-R', '--repo'):
            repo = ['--repo', next(rest, '')]
        elif word.startswith('--repo='):
            repo = [word]
        elif word in value_flags:
            next(rest, None)
        elif not word.startswith('-') and target is None:
            target = word
    return target, repo
