#!/usr/bin/env bash
#
# create-agent-setup.sh -- generate a ready-to-run db-agents setup from a CSV
# of tables plus per-host connection properties files.
#
# Every option can be supplied on the command line (for non-interactive use)
# or read from a defaults file; anything still missing is prompted for.
#
# Run with --help for usage.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

TABLES_CSV=""
CONNECTIONS_DIR=""
TARGET_DIR=""
LLM_DEPLOYMENT=""
PURVIEW_ENDPOINT=""
PURVIEW_ENABLED=""
OPTIONS_FILE=""
NON_INTERACTIVE=0
FORCE=0
SKIP_VENV=0
PYTHON_BIN="${PYTHON_BIN:-python3}"

usage() {
    cat <<'EOF'
Usage: create-agent-setup.sh [options]

Generates a complete db-agents setup (config.yaml, .env skeleton, virtualenv
with db_agents installed, and a run-mcp-server.sh launcher) in a target
directory, based on a CSV listing the tables that should get agents.

Options:
  --options-file FILE     Read defaults from FILE (key=value, same names as the
                          long options below without the leading dashes, e.g.
                          tables_csv=/path/to/tables.csv)
  --tables-csv FILE       CSV with columns: db_type,host,schema,table
  --connections-dir DIR   Directory holding one <host>.properties per host
  --target-dir DIR        Where to create the new setup
  --llm-deployment NAME   Azure OpenAI deployment used for descriptions and Q&A
  --purview-endpoint URL  Purview account endpoint (implies --purview-enabled)
  --purview-enabled       Enable Purview enrichment (prompts for the endpoint)
  --no-purview            Disable Purview enrichment without prompting
  --python PATH           Python interpreter used to create the venv (default: python3)
  --skip-venv             Only generate config files; don't create a virtualenv
  --non-interactive       Never prompt; fail if a required option is missing
  --force                 Overwrite an existing config.yaml / .env
  -h, --help              Show this help

Examples:
  # Fully interactive
  ./scripts/create-agent-setup.sh

  # Fully non-interactive
  ./scripts/create-agent-setup.sh \
      --tables-csv examples/tables.example.csv \
      --connections-dir examples/connections \
      --target-dir ~/db-agents-prod \
      --llm-deployment gpt-4o \
      --no-purview --non-interactive
EOF
}

die() {
    echo "error: $*" >&2
    exit 1
}

# Read defaults from a key=value options file. Command line options given
# before or after --options-file always win, so we only fill in blanks.
load_options_file() {
    local file="$1"
    [[ -f "$file" ]] || die "options file not found: $file"

    local key value
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line#"${line%%[![:space:]]*}"}"
        [[ -z "$line" || "$line" == \#* ]] && continue
        key="${line%%=*}"
        value="${line#*=}"
        key="$(echo "$key" | tr -d '[:space:]' | tr '[:upper:]-' '[:lower:]_')"
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"

        case "$key" in
            tables_csv)        [[ -z "$TABLES_CSV" ]]       && TABLES_CSV="$value" ;;
            connections_dir)   [[ -z "$CONNECTIONS_DIR" ]]  && CONNECTIONS_DIR="$value" ;;
            target_dir)        [[ -z "$TARGET_DIR" ]]       && TARGET_DIR="$value" ;;
            llm_deployment)    [[ -z "$LLM_DEPLOYMENT" ]]   && LLM_DEPLOYMENT="$value" ;;
            purview_endpoint)  [[ -z "$PURVIEW_ENDPOINT" ]] && PURVIEW_ENDPOINT="$value" ;;
            purview_enabled)   [[ -z "$PURVIEW_ENABLED" ]]  && PURVIEW_ENABLED="$value" ;;
            python)            PYTHON_BIN="$value" ;;
            skip_venv)         [[ "$value" =~ ^(1|true|yes)$ ]] && SKIP_VENV=1 ;;
            force)             [[ "$value" =~ ^(1|true|yes)$ ]] && FORCE=1 ;;
            *) echo "warning: ignoring unknown key '$key' in $file" >&2 ;;
        esac
    done < "$file"
}

# Prompt for a value, unless running non-interactively.
prompt_for() {
    local prompt="$1" default="${2:-}" reply
    if (( NON_INTERACTIVE )); then
        return 1
    fi
    if [[ -n "$default" ]]; then
        read -r -p "$prompt [$default]: " reply </dev/tty || return 1
        echo "${reply:-$default}"
    else
        read -r -p "$prompt: " reply </dev/tty || return 1
        echo "$reply"
    fi
}

# Expand a leading ~ so prompted paths behave as users expect.
expand_path() {
    local path="$1"
    [[ "$path" == "~"* ]] && path="${HOME}${path:1}"
    echo "$path"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --options-file)     OPTIONS_FILE="$2"; shift 2 ;;
        --tables-csv)       TABLES_CSV="$2"; shift 2 ;;
        --connections-dir)  CONNECTIONS_DIR="$2"; shift 2 ;;
        --target-dir)       TARGET_DIR="$2"; shift 2 ;;
        --llm-deployment)   LLM_DEPLOYMENT="$2"; shift 2 ;;
        --purview-endpoint) PURVIEW_ENDPOINT="$2"; PURVIEW_ENABLED="yes"; shift 2 ;;
        --purview-enabled)  PURVIEW_ENABLED="yes"; shift ;;
        --no-purview)       PURVIEW_ENABLED="no"; shift ;;
        --python)           PYTHON_BIN="$2"; shift 2 ;;
        --skip-venv)        SKIP_VENV=1; shift ;;
        --non-interactive)  NON_INTERACTIVE=1; shift ;;
        --force)            FORCE=1; shift ;;
        -h|--help)          usage; exit 0 ;;
        *) die "unknown option: $1 (use --help)" ;;
    esac
done

[[ -n "$OPTIONS_FILE" ]] && load_options_file "$OPTIONS_FILE"

echo "== db-agents setup generator =="

if [[ -z "$TABLES_CSV" ]]; then
    TABLES_CSV="$(prompt_for "Path to the tables CSV" "${PROJECT_ROOT}/examples/tables.example.csv")" \
        || die "--tables-csv is required"
fi
TABLES_CSV="$(expand_path "$TABLES_CSV")"
[[ -f "$TABLES_CSV" ]] || die "tables CSV not found: $TABLES_CSV"

if [[ -z "$CONNECTIONS_DIR" ]]; then
    CONNECTIONS_DIR="$(prompt_for "Directory with <host>.properties files" "$(dirname "$TABLES_CSV")/connections")" \
        || die "--connections-dir is required"
fi
CONNECTIONS_DIR="$(expand_path "$CONNECTIONS_DIR")"
[[ -d "$CONNECTIONS_DIR" ]] || die "connections directory not found: $CONNECTIONS_DIR"

if [[ -z "$TARGET_DIR" ]]; then
    TARGET_DIR="$(prompt_for "Where should the new agent setup be created?" "$HOME/db-agents-setup")" \
        || die "--target-dir is required"
fi
TARGET_DIR="$(expand_path "$TARGET_DIR")"
[[ -n "$TARGET_DIR" ]] || die "--target-dir must not be empty"

if [[ -z "$LLM_DEPLOYMENT" ]] && (( ! NON_INTERACTIVE )); then
    LLM_DEPLOYMENT="$(prompt_for "Azure OpenAI deployment name (blank to skip)" "")" || true
fi

if [[ -z "$PURVIEW_ENABLED" ]] && (( ! NON_INTERACTIVE )); then
    PURVIEW_ENABLED="$(prompt_for "Enable Microsoft Purview enrichment? (yes/no)" "no")" || true
fi
PURVIEW_ENABLED_LC="$(printf '%s' "$PURVIEW_ENABLED" | tr '[:upper:]' '[:lower:]')"
if [[ "$PURVIEW_ENABLED_LC" =~ ^(y|yes|true|1)$ && -z "$PURVIEW_ENDPOINT" ]]; then
    PURVIEW_ENDPOINT="$(prompt_for "Purview account endpoint (e.g. https://myaccount.purview.azure.com)" "")" \
        || die "--purview-endpoint is required when Purview is enabled"
    [[ -n "$PURVIEW_ENDPOINT" ]] || die "--purview-endpoint is required when Purview is enabled"
fi

command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "python interpreter not found: $PYTHON_BIN"

mkdir -p "$TARGET_DIR"
TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"
echo "Target directory: $TARGET_DIR"

# ---------------------------------------------------------------------------
# 1. Work out which database drivers we need, using the generator itself so
#    the CSV is validated before we spend time building a virtualenv.
# ---------------------------------------------------------------------------
DIALECTS="$(PYTHONPATH="$PROJECT_ROOT" "$PYTHON_BIN" -m db_agents.setup_cli \
    --tables-csv "$TABLES_CSV" \
    --connections-dir "$CONNECTIONS_DIR" \
    --print-dialects)" || die "failed to parse the input files (see message above)"

echo "Database technologies in use: $(echo "$DIALECTS" | tr '\n' ' ')"

EXTRAS=""
for dialect in $DIALECTS; do
    EXTRAS="${EXTRAS:+$EXTRAS,}$dialect"
done

# ---------------------------------------------------------------------------
# 2. Create the virtualenv and install db_agents with just those extras.
# ---------------------------------------------------------------------------
VENV_DIR="$TARGET_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"

if (( SKIP_VENV )); then
    echo "Skipping virtualenv creation (--skip-venv)."
    GENERATOR_PYTHON="$PYTHON_BIN"
    GENERATOR_ENV=(env "PYTHONPATH=$PROJECT_ROOT")
else
    if [[ ! -x "$VENV_PYTHON" ]]; then
        echo "Creating virtualenv in $VENV_DIR ..."
        "$PYTHON_BIN" -m venv "$VENV_DIR"
    else
        echo "Reusing existing virtualenv in $VENV_DIR"
    fi

    echo "Installing db_agents[$EXTRAS] ..."
    "$VENV_PYTHON" -m pip install --upgrade pip >/dev/null
    if ! "$VENV_PYTHON" -m pip install "${PROJECT_ROOT}[${EXTRAS}]"; then
        echo "warning: installing with database driver extras failed; falling back to the base package." >&2
        echo "         Install the drivers you need manually, e.g.: $VENV_PYTHON -m pip install '${PROJECT_ROOT}[postgresql]'" >&2
        "$VENV_PYTHON" -m pip install "$PROJECT_ROOT"
    fi
    GENERATOR_PYTHON="$VENV_PYTHON"
    GENERATOR_ENV=(env)
fi

# ---------------------------------------------------------------------------
# 3. Generate config.yaml and the .env skeleton.
# ---------------------------------------------------------------------------
GEN_ARGS=(
    -m db_agents.setup_cli
    --tables-csv "$TABLES_CSV"
    --connections-dir "$CONNECTIONS_DIR"
    --target-dir "$TARGET_DIR"
)
[[ -n "$LLM_DEPLOYMENT" ]] && GEN_ARGS+=(--llm-deployment "$LLM_DEPLOYMENT")
[[ -n "$PURVIEW_ENDPOINT" ]] && GEN_ARGS+=(--purview-endpoint "$PURVIEW_ENDPOINT")
(( FORCE )) && GEN_ARGS+=(--force)

"${GENERATOR_ENV[@]}" "$GENERATOR_PYTHON" "${GEN_ARGS[@]}"

# Keep a copy of the inputs so the setup can be regenerated later.
mkdir -p "$TARGET_DIR/inputs"
cp "$TABLES_CSV" "$TARGET_DIR/inputs/tables.csv"
rm -rf "$TARGET_DIR/inputs/connections"
cp -R "$CONNECTIONS_DIR" "$TARGET_DIR/inputs/connections"

# ---------------------------------------------------------------------------
# 4. Write the launcher and a short README.
# ---------------------------------------------------------------------------
LAUNCHER="$TARGET_DIR/run-mcp-server.sh"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Starts the db-agents MCP server for this setup.
set -euo pipefail

HERE="\$(cd -- "\$(dirname -- "\${BASH_SOURCE[0]}")" && pwd)"
cd "\$HERE"

if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

export DB_AGENTS_CONFIG="\${DB_AGENTS_CONFIG:-\$HERE/config.yaml}"

PYTHON="\$HERE/.venv/bin/python"
[[ -x "\$PYTHON" ]] || PYTHON="$PYTHON_BIN"

# Prefer the installed console script; fall back to the module for a
# --skip-venv setup where db_agents is only importable, not installed.
CONSOLE="\$HERE/.venv/bin/db-agents-mcp"
if [[ -x "\$CONSOLE" ]]; then
    exec "\$CONSOLE" "\$@"
fi

exec "\$PYTHON" -c 'from db_agents.mcp_server.server import main; main()' "\$@"
EOF
chmod +x "$LAUNCHER"

cat > "$TARGET_DIR/README.md" <<EOF
# db-agents setup

Generated by \`create-agent-setup.sh\` from:

- tables CSV: \`$TABLES_CSV\`
- connections: \`$CONNECTIONS_DIR\`

A copy of both is kept in \`inputs/\` so this setup can be regenerated.

## Files

| File | Purpose |
| --- | --- |
| \`config.yaml\` | Connections, table filters, LLM and Purview settings |
| \`.env\` | Secrets and endpoints (never commit this) |
| \`run-mcp-server.sh\` | Starts the MCP server with this config |
| \`inputs/\` | The CSV and properties files used to generate the setup |

## Next steps

1. Fill in the blanks in \`.env\` (database passwords, Azure OpenAI endpoint and key).
2. Start the server:

   \`\`\`bash
   ./run-mcp-server.sh
   \`\`\`

3. Point your MCP client at that command. Available tools: \`list_tables\`,
   \`describe_table\`, \`refresh_metadata\`, \`ask_question\`.

## Regenerating

\`\`\`bash
$SCRIPT_DIR/create-agent-setup.sh \\
    --tables-csv "$TARGET_DIR/inputs/tables.csv" \\
    --connections-dir "$TARGET_DIR/inputs/connections" \\
    --target-dir "$TARGET_DIR" \\
    --non-interactive --force
\`\`\`
EOF

echo
echo "Done. Setup created in: $TARGET_DIR"
echo
echo "Next steps:"
echo "  1. Edit $TARGET_DIR/.env and fill in the secrets."
echo "  2. Run: $LAUNCHER"
