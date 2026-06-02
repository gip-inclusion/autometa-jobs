#!/usr/bin/env bash
set -euo pipefail

# Setup autometa-jobs : stocke les accès et installe le lanceur `jobsctl`.
# À lancer une fois. Renseignez les deux valeurs ci-dessous (fournies par
# l'administrateur), ou laissez-les vides pour être invité à les saisir.

PIPOMETA_URL=""
PIPOMETA_API_KEY=""

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autometa-jobs"
BIN_DIR="$HOME/.local/bin"

command -v uv >/dev/null 2>&1 || { echo "uv est requis : https://docs.astral.sh/uv/" >&2; exit 1; }

[ -n "$PIPOMETA_URL" ] || read -rp "PIPOMETA_URL: " PIPOMETA_URL
[ -n "$PIPOMETA_API_KEY" ] || { read -rsp "PIPOMETA_API_KEY: " PIPOMETA_API_KEY; echo; }

mkdir -p "$CONFIG_DIR"
cat > "$CONFIG_DIR/config.env" <<EOF
PIPOMETA_URL=$PIPOMETA_URL
PIPOMETA_API_KEY=$PIPOMETA_API_KEY
EOF
chmod 600 "$CONFIG_DIR/config.env"

mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/jobsctl" <<EOF
#!/usr/bin/env bash
exec uv run --quiet --project "$REPO/jobsctl" jobsctl "\$@"
EOF
chmod +x "$BIN_DIR/jobsctl"

echo "✓ Accès écrits dans $CONFIG_DIR/config.env"
echo "✓ Lanceur installé dans $BIN_DIR/jobsctl"
case ":$PATH:" in
  *":$BIN_DIR:"*) echo "→ Lancez : jobsctl pipelines" ;;
  *) echo "⚠ Ajoutez $BIN_DIR à votre PATH, puis : jobsctl pipelines"
     echo "   (ex. dans ~/.zshrc : export PATH=\"$BIN_DIR:\$PATH\")" ;;
esac
