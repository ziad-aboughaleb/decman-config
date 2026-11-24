"""Desktop: Hyprland session, display manager, theming, and GUI essentials."""

PACKAGES = [
  # compositor & session
  "hyprland",
  "noctalia",
  "qt6-5compat",
  "sddm",
  "uwsm",
  "xdg-desktop-portal-hyprland",
  # essentials
  "ghostty",
  "ghostty-nautilus",
  "hyprpicker",
  "nautilus",
  "paru",
  "zsh",
  # theming & fonts
  "adw-gtk-theme",
  "aur:bibata-cursor-theme-bin",
  "noto-fonts",
  "noto-fonts-emoji",
  "papirus-icon-theme",
  "terminus-font",
  "ttf-dejavu",
  "ttf-jetbrains-mono-nerd",
  "ttf-liberation",
]

SYSTEMD_UNITS = [
  "sddm.service",
  "upower.service",
]
