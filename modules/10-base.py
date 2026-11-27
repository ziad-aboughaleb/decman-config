"""Base system: kernel, boot, networking, audio, core daemons.

Everything here is machine-independent. Hardware-conditional packages live in
00-hardware.py; per-user applications live in desktop.py, dev.py, apps.py, and gaming.py.
"""

PACKAGES = [
  # aur / meta & package management
  "aur:decman",
  "devtools",
  # bootloader, filesystems & snapshots
  "btrfs-progs",
  "dosfstools",
  "efibootmgr",
  "limine",
  "limine-snapper-sync",
  "snap-pac",
  "snapper",
  # kernel & core tooling
  "base",
  "base-devel",
  "cachyos-keyring",
  "cachyos-mirrorlist",
  "cachyos-rate-mirrors",
  "cachyos-v3-mirrorlist",
  "cachyos-v4-mirrorlist",
  "linux-cachyos",
  "linux-cachyos-headers",
  "linux-firmware",
  "mkinitcpio",
  "sof-firmware",
  # networking & security
  "bluez",
  "bluez-utils",
  "gnome-keyring",
  "networkmanager",
  "iwd",
  "sbctl",
  "sudo",
  "ufw",
  # audio (pipewire)
  "gst-plugin-pipewire",
  "libpulse",
  "pavucontrol",
  "pipewire",
  "pipewire-alsa",
  "pipewire-jack",
  "pipewire-pulse",
  "wireplumber",
  # system daemons
  "zram-generator",
]

SYSTEMD_UNITS = [
  "NetworkManager.service",
  "bluetooth.service",
  "cachyos-rate-mirrors.timer",
  "fstrim.timer",
  "getty@tty1.service",
  "getty@tty2.service",
  "getty@tty3.service",
  "getty@tty4.service",
  "snapper-cleanup.timer",
  "systemd-timesyncd.service",
  "ufw.service",
]

SYSTEMD_USER_UNITS = [
  "pipewire-pulse.socket",
  "pipewire.socket",
  "wireplumber.service",
]
