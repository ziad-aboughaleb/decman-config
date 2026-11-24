#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Installing base dependencies..."
sudo pacman -Syu --needed git base-devel devtools python

# Install decman from AUR
if ! command -v decman &>/dev/null; then
  echo "==> Installing decman..."
  tmpdir=$(mktemp -d)
  git clone https://aur.archlinux.org/decman.git "$tmpdir/decman"
  (cd "$tmpdir/decman" && makepkg -si --noconfirm)
  rm -rf "$tmpdir"
fi

# Optional user creation (if running on a fresh root-only install)
read -r -p "==> Create/configure user (leave blank for '$USER'): " TARGET_USER
if [[ -n "$TARGET_USER" ]] && ! id "$TARGET_USER" &>/dev/null; then
  sudo useradd -m -G wheel "$TARGET_USER"
  sudo passwd "$TARGET_USER"
fi

# Apply declarative system & dotfiles
echo "==> Running decman..."
sudo decman --source "$CONFIG_DIR/source.py"

echo "==> Setup complete!"
