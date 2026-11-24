# decman-config

Declarative Arch/CachyOS Linux system configuration repo powered by [`decman`](https://aur.archlinux.org/packages/decman).

## Overview

`decman-config` maintains your entire system state declaratively through Python-based modules and version-controlled config files:

- **Modular Specifications**: `source.py` dynamically loads modular package and unit definitions from `./modules/*.py` (e.g., `00-hardware`, `10-base`, `50-desktop`, `60-dev`, `70-gaming`, `80-apps`).
- **System File Deployment**: Deploys system configuration files from `./files` to `/` (owned by `root:root`) with support for custom octal permission prefixes (e.g., `440.wheel` → `/etc/sudoers.d/wheel` with mode `0o440`).
- **Dotfile Management**: Automatically symlinks dotfiles from `./home` to your user home directory (`~`).
- **Live `/etc` Diff & Pullbacks**: Pulls live edits in managed system files back into the repo interactively before overwriting them.
- **Drift Detection**: Automatically scans `/etc` and `/usr/local` for unmanaged system drift (ignoring system-generated states and caches via `IGNORED_PATHS`).

---

## Repository Structure

```text
decman-config/
├── source.py          # Decman entrypoint & dynamic module loader
├── setup.sh           # Bootstrap script for fresh installations
├── modules/           # Declarative package & unit definitions
│   ├── 00-hardware.py
│   ├── 10-base.py
│   ├── 50-desktop.py
│   ├── 60-dev.py
│   ├── 70-gaming.py
│   └── 80-apps.py
├── files/             # System files deployed to / (etc/, usr/, etc.)
│   ├── etc/
│   │   ├── sudoers.d/440.wheel
│   │   └── ...
│   └── usr/
└── home/              # User dotfiles symlinked to ~
    ├── .config/
    └── ...
```

---

## Bootstrap / Fresh Install

1. Clone your repository:
   ```bash
   git clone https://github.com/<your-username>/decman-config.git ~/.local/share/src/decman-config
   cd ~/.local/share/src/decman-config
   ```
2. Run the bootstrap setup script:
   ```bash
   ./setup.sh
   ```
3. Apply the system configuration:
   ```bash
   sudo decman --source source.py
   ```
   > **Note**: `decman` automatically persists the active source path in `/var/lib/decman/store.json`, so subsequent runs only require `sudo decman`.

---

## Daily Usage

| Command                           | Description                                                                                                                    |
| :-------------------------------- | :----------------------------------------------------------------------------------------------------------------------------- |
| `sudo decman`                     | Syncs system files, symlinks, systemd system/user units, and package additions/removals without running full package upgrades. |
| `sudo decman --params upgrade`    | Runs full system package upgrades (`pacman -Syu`) and AUR package updates.                                                     |
| `DECMAN_SKIP_DRIFT=1 sudo decman` | Runs synchronization while bypassing `/etc` & `/usr/local` drift scanning.                                                     |
| `sudo decman --dry-run`           | Previews all pending system changes non-interactively without applying them.                                                   |

---

## Conventions & Module Authoring

- **Packages**: Define packages in modules under `modules/*.py`. Native Arch packages use plain names, while AUR packages require the `aur:` prefix (e.g., `"aur:yay-bin"`).
- **Systemd Units**: Declare system and user services via `SYSTEMD_UNITS` and `SYSTEMD_USER_UNITS` lists in module files.
- **File Permissions**: Prefix filenames in `./files` with octal permission modes (e.g., `440.wheel` compiles to mode `0o440` owned by `wheel`). Standard files default to `0o644` (or `0o755` if executable).
- **Drift Exclusions**: Customize paths exempt from drift checks using `IGNORED_PATHS` regex/glob patterns inside `source.py`.
