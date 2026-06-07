#!/usr/bin/env bash
# Install remote-debugger profile from hermes-agent repo.
# Run from repo root: bash examples/remote-debugger/install-profile.sh

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EXAMPLE_DIR="$REPO_ROOT/examples/remote-debugger"
PROFILE_DIR="${HOME}/.hermes/profiles/remote-debugger"
SKILL_DEST="$PROFILE_DIR/skills/software-development/remote-ai-debugger"

mkdir -p "$SKILL_DEST"
cp "$EXAMPLE_DIR/config.yaml.example" "$PROFILE_DIR/config.yaml"
cp "$EXAMPLE_DIR/mcp_servers.example.yaml" "$PROFILE_DIR/mcp_servers.fragment.yaml"
cp "$EXAMPLE_DIR/.env.example" "$PROFILE_DIR/.env"
cp "$REPO_ROOT/skills/software-development/remote-ai-debugger/SKILL.md" "$SKILL_DEST/SKILL.md"
cp "$EXAMPLE_DIR/README.zh.md" "$PROFILE_DIR/README.zh.md"
cp "$EXAMPLE_DIR/PLAN.zh.md" "$PROFILE_DIR/PLAN.zh.md"
cp "$EXAMPLE_DIR/REQUIREMENTS.zh.md" "$PROFILE_DIR/REQUIREMENTS.zh.md"

echo "Installed profile to $PROFILE_DIR"
echo "Next: edit .env (TERMINAL_SSH_*), then: hermes -p remote-debugger doctor"
