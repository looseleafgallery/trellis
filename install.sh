#!/bin/sh
# Install trellis as a global `trellis` command — PATH included.
#
#   curl -LsSf https://raw.githubusercontent.com/looseleafgallery/trellis/main/install.sh | sh
#
# POSIX sh on purpose: the line above pipes to `sh`, so this cannot be bash.
#
# Idempotent. Re-running upgrades to whatever `main` is now; re-running with
# nothing to do changes nothing but the report. Never installs a package
# manager without saying so first — pass --no-install-uv to refuse.
set -eu

REPO_URL="git+https://github.com/looseleafgallery/trellis.git"
UV_INSTALLER_URL="https://astral.sh/uv/install.sh"

install_uv=1

usage() {
    cat <<'EOF'
usage: install.sh [--no-install-uv]

Installs trellis as a global `trellis` command with uv, puts the directory it
lands in on PATH, and verifies the command actually runs.

  --no-install-uv  Stop rather than install uv, if uv is not already present.
  -h, --help       This message.

Piping to sh takes options after `-s --`:

  curl -LsSf https://raw.githubusercontent.com/looseleafgallery/trellis/main/install.sh | sh -s -- --no-install-uv
EOF
}

say() { printf '%s\n' "$*"; }
warn() { printf '%s\n' "$*" >&2; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --no-install-uv) install_uv=0 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
    shift
done

fetch() {
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- "$1"
    else
        die "need curl or wget to download $1"
    fi
}

# -- find uv ----------------------------------------------------------------

# uv may be installed and not yet on PATH: that is the exact state this script
# leaves behind between installing uv and the user's next login shell, so a
# second run in the same terminal must find it rather than install it twice.
find_uv() {
    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return 0
    fi
    for dir in "${XDG_BIN_HOME:-}" "${XDG_DATA_HOME:-}/../bin" "$HOME/.local/bin" \
               "$HOME/.cargo/bin"; do
        case "$dir" in ""|"/../bin") continue ;; esac
        if [ -x "$dir/uv" ]; then
            printf '%s\n' "$dir/uv"
            return 0
        fi
    done
    return 1
}

uv="$(find_uv || true)"

if [ -z "$uv" ]; then
    uv_target="${XDG_BIN_HOME:-$HOME/.local/bin}"
    if [ "$install_uv" -eq 0 ]; then
        warn "uv is not installed, and --no-install-uv was given."
        warn ""
        warn "Install uv yourself and re-run, or use the manual fallback:"
        warn "    pipx install $REPO_URL"
        exit 1
    fi
    say "uv is not installed. trellis uses it to pick a Python and to install"
    say "the command globally."
    say ""
    say "  installing:  uv, the Python package manager from Astral"
    say "  from:        $UV_INSTALLER_URL"
    say "  into:        $uv_target"
    say "  to refuse:   re-run with --no-install-uv"
    say ""

    # UV_NO_MODIFY_PATH: uv's own installer edits shell profiles too. PATH is
    # settled in one place below, so that this script has one file to name
    # rather than two mechanisms to reconcile.
    if ! uv_install_log="$(fetch "$UV_INSTALLER_URL" | UV_NO_MODIFY_PATH=1 sh 2>&1)"; then
        printf '%s\n' "$uv_install_log" >&2
        die "installing uv failed"
    fi

    uv="$(find_uv || true)"
    [ -n "$uv" ] || die "uv installer finished but no uv binary was found in $uv_target"
    say "installed  uv at $uv"
    say ""
fi

# -- install trellis --------------------------------------------------------

say "installing trellis from $REPO_URL"

# --force so that re-running upgrades to current main rather than reporting
# that a tool by this name already exists and leaving an old build in place.
if ! tool_install_log="$("$uv" tool install --force "$REPO_URL" 2>&1)"; then
    printf '%s\n' "$tool_install_log" >&2
    die "uv could not install trellis"
fi

bin_dir="$("$uv" tool dir --bin 2>/dev/null || true)"
[ -n "$bin_dir" ] || bin_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"

# -- settle PATH ------------------------------------------------------------

# Which profile file gets written is uv's decision and depends on the shell.
# Rather than guess it and risk naming a file that was not touched, take a
# checksum of every candidate before and after and report what actually moved.
profile_candidates() {
    cat <<EOF
$HOME/.profile
$HOME/.bashrc
$HOME/.bash_profile
$HOME/.bash_login
$HOME/.zshenv
$HOME/.zprofile
$HOME/.zshrc
$HOME/.config/fish/config.fish
$HOME/.config/nushell/env.nu
$HOME/.config/elvish/rc.elv
$HOME/.cshrc
$HOME/.tcshrc
EOF
}

snapshot_profiles() {
    profile_candidates | while IFS= read -r file; do
        if [ -f "$file" ]; then
            printf '%s %s\n' "$file" "$(cksum < "$file")"
        else
            printf '%s absent\n' "$file"
        fi
    done
}

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT INT TERM

snapshot_profiles > "$work_dir/before"

# Not `if ! ...`: `uv tool update-shell` exits non-zero when the profiles are
# already correct but this process's own PATH does not have the directory yet
# — which is the normal state of a second run, and not a failure. Its exit
# code answers a different question than the one being asked, so the message
# is kept and only shown if the verification below actually finds a problem.
update_shell_log=""
update_shell_log="$("$uv" tool update-shell 2>&1)" || :

snapshot_profiles > "$work_dir/after"

touched="$(awk '
    NR == FNR { before[$1] = substr($0, length($1) + 2); next }
    { after = substr($0, length($1) + 2); if (before[$1] != after) print $1 }
' "$work_dir/before" "$work_dir/after")"

# Which profiles put the directory on PATH, whether or not this run wrote
# them. Without this, a second run reports "no profile was changed" and would
# go on to tell the user to add a line that is already there. uv writes the
# directory as a literal path, so an exact match is enough.
referencing="$(profile_candidates | while IFS= read -r file; do
    if [ -f "$file" ] && grep -q -F -e "$bin_dir" "$file"; then
        printf '%s\n' "$file"
    fi
done)"

if [ -n "$touched" ]; then
    printf '%s\n' "$touched" | while IFS= read -r file; do
        say "modified   $file  (added $bin_dir to PATH)"
    done
elif [ -n "$referencing" ]; then
    printf '%s\n' "$referencing" | while IFS= read -r file; do
        say "unchanged  $file  (already puts $bin_dir on PATH)"
    done
fi

# -- verify -----------------------------------------------------------------

# The point of this script, not its epilogue. An installer that reports
# success while `trellis` is not runnable is the failure CONTRIBUTING.md
# records as "green output is not a green run" — absence of evidence reported
# as evidence. So run it, and describe only what was observed.

trellis_bin="$bin_dir/trellis"
[ -x "$trellis_bin" ] || die "uv reported success but there is no executable at $trellis_bin"

# Not `command -v`: that it is on PATH is a weaker claim than that it runs.
# This import also crosses the Python version guard and PyYAML.
if ! version_output="$("$trellis_bin" --version 2>&1)"; then
    printf '%s\n' "$version_output" >&2
    die "trellis is installed at $trellis_bin but will not run"
fi

shell_path="${SHELL:-}"
shell_name="your shell"
if [ -n "$shell_path" ]; then
    shell_name="$(basename "$shell_path")"
fi

# What a *new* shell would resolve, which is the question the user actually
# has. An earlier PATH entry can shadow this install with a different trellis,
# and then "trellis resolves" is true and useless.
#
# probe_ran is tracked separately because "asked a new shell and it found
# nothing" and "had no shell to ask" are different findings, and reporting the
# second as the first would be inventing a result.
probe_ran=0
fresh_resolved=""
if [ -n "$shell_path" ] && [ -x "$shell_path" ]; then
    probe_ran=1
    fresh_resolved="$("$shell_path" -lc 'command -v trellis' </dev/null 2>/dev/null | tail -n 1 || true)"
fi

say ""
say "installed  $version_output"
say "           $trellis_bin"

if [ "$fresh_resolved" = "$trellis_bin" ]; then
    say "verified   runs, and a new $shell_name shell resolves \`trellis\` to it"
elif [ -n "$fresh_resolved" ]; then
    say "verified   runs at the path above"
    say ""
    warn "but a new $shell_name shell resolves \`trellis\` to a different install:"
    warn "    $fresh_resolved"
    warn ""
    warn "That one shadows this one, and is what you would get by typing"
    warn "\`trellis\`. Remove it, or put $bin_dir earlier on PATH."
else
    say "verified   runs at the path above"
    say ""
    if [ "$probe_ran" -eq 1 ]; then
        say "\`trellis\` does not resolve in a new $shell_name shell yet."
    else
        say "Whether a new shell resolves \`trellis\` was not checked: SHELL is"
        say "not set, so there was no shell to ask."
    fi
    say ""
    if [ -n "$touched" ]; then
        first_profile="$(printf '%s\n' "$touched" | head -n 1)"
        say "$bin_dir was added to $first_profile."
        say "Open a new $shell_name, or use it in this one with:"
        say ""
        say "    . $first_profile"
    elif [ -n "$referencing" ]; then
        first_profile="$(printf '%s\n' "$referencing" | head -n 1)"
        say "$first_profile already puts $bin_dir on PATH."
        if [ "$probe_ran" -eq 1 ]; then
            say "A new $shell_name should be reading it and is not — check that"
            say "$shell_name reads that file at startup."
        fi
        say "In this shell, this works now:"
        say ""
        say "    . $first_profile"
    else
        say "No profile file puts $bin_dir on PATH. Add this to the"
        say "file $shell_name reads at startup:"
        say ""
        say "    export PATH=\"$bin_dir:\$PATH\""
    fi
    if [ -n "$update_shell_log" ]; then
        say ""
        say "uv reported, while settling PATH:"
        printf '%s\n' "$update_shell_log" | while IFS= read -r line; do
            say "    $line"
        done
    fi
fi

say ""
say "next:      trellis brief        # the operating manual"
say "           trellis state        # from a directory with a graph/"
say ""
# The distribution is `trellis-kernel`; the command, the import and the tool
# are all `trellis`. uv knows it by the distribution name.
say "uninstall: $uv tool uninstall trellis-kernel"
if [ -n "$touched" ] || [ -n "$referencing" ]; then
    say "           (the PATH line stays in your profile; remove it by hand)"
fi
