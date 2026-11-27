"""Decman declarative system engine driven by ./modules/*.py."""

from __future__ import annotations


def main() -> None:
  import dataclasses
  import datetime
  import difflib
  import fnmatch
  import json
  import os
  import pathlib
  import re
  import runpy
  import shutil
  import subprocess
  import sys
  import urllib.parse

  import decman

  if (pacman := decman.pacman) is None:
    raise decman.SourceError("pacman plugin unavailable.")

  # --- Config & Constants -----------------------------------------------------

  STORE_FILE = pathlib.Path("/var/lib/decman/store.json")
  SYS_DIRS = ("/etc", "/usr/local")
  BINARY_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".ico", ".gif", ".bin", ".ttf", ".otf", ".woff2"}
  SKIP_WALK_DIRS = {".cache", ".git", ".cargo", ".rustup", ".npm", "Trash", "Steam", "umu", "node_modules"}
  MODE_PREFIX = re.compile(r"^([0-7]{3,4})\.(.+)$")

  COLORS = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "cyan": "36"}
  DIFF_COLORS = {"+++": ("bold",), "---": ("bold",), "@@": ("cyan",), "+": ("green",), "-": ("red",)}

  def resolve_config_dir() -> pathlib.Path:
    candidates: list[pathlib.Path] = []
    if "__file__" in globals():
      candidates.append(pathlib.Path(__file__).resolve().parent)
    if STORE_FILE.exists() and (saved := json.loads(STORE_FILE.read_text()).get("source_file")):
      candidates.append(pathlib.Path(saved).resolve().parent)
    candidates.append(pathlib.Path.cwd().resolve())

    for path in candidates:
      if (path / "modules").is_dir() and ((path / "files").is_dir() or (path / "home").is_dir()):
        return path
    raise decman.SourceError("Could not locate decman-config repository directory.")

  CONFIG_DIR = resolve_config_dir()
  MODULES_DIR = CONFIG_DIR / "modules"
  FILES_DIR = CONFIG_DIR / "files"
  HOME_DIR = CONFIG_DIR / "home"

  USER = os.environ.get("SUDO_USER") or "ziad"
  HOME = pathlib.Path(f"/home/{USER}")
  TRASH_DIR = HOME / ".local/share/Trash"

  # --- System Drift Exclusions ------------------------------------------------

  _IGNORE_PATTERNS = (
    # Identity, auth & machine-unique state
    "/etc/passwd*",
    "/etc/shadow*",
    "/etc/group*",
    "/etc/gshadow*",
    "/etc/subuid*",
    "/etc/subgid*",
    "/etc/sudoers",
    "/etc/sudoers.d/*",
    "/etc/.pwd.lock",
    "/etc/fstab",
    "/etc/crypttab",
    "/etc/machine-id",
    "/etc/.updated",
    "/etc/hostname",
    "/etc/localtime",
    "/etc/locale.gen",
    "/etc/resolv.conf",
    "/etc/os-release",
    "/etc/adjtime",
    # Boot & snapshots
    "/etc/mkinitcpio.conf",
    "/etc/mkinitcpio.d/*",
    "/etc/conf.d/snapper",
    # Keys, certs & binary hardware/linker caches
    "/etc/ssh/ssh_host_*",
    "/etc/pacman.d/gnupg/*",
    "/etc/ssl/*",
    "/etc/ca-certificates/*",
    "/etc/ld.so.cache",
    "/etc/udev/hwdb.bin",
    # Stateful network, bluetooth & generated symlinks
    "/etc/NetworkManager/system-connections/*",
    "/etc/iwd/*",
    "/etc/bluetooth/*",
    "/etc/fonts/conf.d/*",
    "/etc/X11/xorg.conf.d/00-keyboard.conf",
    "/etc/pacman.d/*mirrorlist*",
    "/etc/pacman.d/*-backup",
    "/etc/systemd/*/*.wants/*",
    "/etc/systemd/*/*.service",
    # /usr/local desktop/mime caches
    "/usr/local/share/applications/mimeinfo.cache",
    "/usr/local/share/mime/*",
    # Generic backup, log & cache artifacts
    "*.pacsave",
    "*.pacnew",
    "*.old",
    "*.bak",
    "*.log",
    "*history*",
    "*cache*",
  )

  IGNORE_RE = re.compile("|".join(fnmatch.translate(p.rstrip("/")) for p in _IGNORE_PATTERNS))

  # --- Helpers & Shared State -------------------------------------------------

  def c(text: object, *styles: str) -> str:
    if not sys.stdout.isatty():
      return str(text)
    codes = [COLORS[s] for s in styles if s in COLORS]
    return f"\033[{';'.join(codes)}m{text}\033[0m" if codes else str(text)

  def cmd_lines(*args: str) -> list[str] | None:
    res = subprocess.run(args, capture_output=True, text=True, check=False)
    return res.stdout.splitlines() if res.returncode == 0 else None

  def prompt_yes(message: str) -> bool:
    return input(f"{message} ").strip().lower() == "y"

  @dataclasses.dataclass
  class State:
    upgrade: bool = dataclasses.field(init=False)
    dry_run: bool = dataclasses.field(init=False)
    interactive: bool = dataclasses.field(init=False)
    aur_ok: bool = dataclasses.field(default_factory=lambda: decman.aur is not None)
    modules: list[str] = dataclasses.field(default_factory=list)
    pkgs: set[str] = dataclasses.field(default_factory=set)
    aur_pkgs: set[str] = dataclasses.field(default_factory=set)
    units: set[str] = dataclasses.field(default_factory=set)
    user_units: set[str] = dataclasses.field(default_factory=set)
    change_hooks: list[str] = dataclasses.field(default_factory=list)
    post_hooks: list[str] = dataclasses.field(default_factory=list)
    pkg_drift: list[str] = dataclasses.field(default_factory=list)
    sys_drift: list[str] = dataclasses.field(default_factory=list)

    def __post_init__(self) -> None:
      tokens = {part for arg in sys.argv[1:] for part in arg.split(",")}
      self.upgrade = "upgrade" in tokens
      self.dry_run = bool(tokens & {"--dry-run", "--print"})
      self.interactive = sys.stdin.isatty() and not self.dry_run

  STATE = State()

  # --- Module Loading & File Mapping ------------------------------------------

  def load_modules() -> None:
    paths = [p for p in sorted(MODULES_DIR.glob("*.py")) if not p.name.startswith("_")]
    if not paths:
      raise decman.SourceError(f"No modules found in {MODULES_DIR}")

    STATE.modules = [p.stem for p in paths]
    change_hooks: dict[str, None] = {}
    post_hooks: dict[str, None] = {}
    mod_globals = {"USER": USER, "HOME": str(HOME)}

    for path in paths:
      mod = runpy.run_path(str(path), init_globals=mod_globals)
      for pkg in mod.get("PACKAGES", ()):
        target = STATE.aur_pkgs if pkg.startswith("aur:") else STATE.pkgs
        target.add(pkg.removeprefix("aur:"))

      STATE.units.update(mod.get("SYSTEMD_UNITS", ()))
      STATE.user_units.update(mod.get("SYSTEMD_USER_UNITS", ()))
      change_hooks.update(dict.fromkeys(mod.get("CHANGE_HOOKS", ())))
      post_hooks.update(dict.fromkeys(mod.get("POST_HOOKS", ())))

    STATE.change_hooks = list(change_hooks)
    STATE.post_hooks = list(post_hooks)

  def map_tree(root: pathlib.Path, prefix: str, owner: str, *, symlink: bool = False) -> None:
    if not root.is_dir():
      return

    for path in sorted(root.rglob("*")):
      if path.is_dir():
        continue

      rel = path.relative_to(root)
      if symlink:
        decman.symlinks[f"{prefix}/{rel.as_posix()}"] = str(path)
        continue

      if match := MODE_PREFIX.match(rel.name):
        dst_rel = rel.with_name(match.group(2)).as_posix()
        mode = int(match.group(1), 8)
      else:
        dst_rel = rel.as_posix()
        mode = 0o755 if os.access(path, os.X_OK) else 0o644

      decman.files[f"{prefix}/{dst_rel}"] = decman.File(
        source_file=str(path),
        owner=owner,
        group=owner,
        permissions=mode,
        bin_file=path.suffix.lower() in BINARY_EXTS,
      )

  # --- Audits & Cleanup -------------------------------------------------------

  def clean_broken_symlinks() -> None:
    if STATE.dry_run:
      return

    for root, dirs, files in HOME.walk(on_error=lambda _: None):
      dirs[:] = [d for d in dirs if d not in SKIP_WALK_DIRS]
      for name in (*dirs, *files):
        entry = root / name
        if entry.is_symlink() and not entry.exists():
          raw_target = os.readlink(entry)
          target = pathlib.Path(os.path.abspath(raw_target if os.path.isabs(raw_target) else root / raw_target))
          if target.is_relative_to(CONFIG_DIR):
            entry.unlink(missing_ok=True)

  def run_audits() -> None:
    if (explicit := cmd_lines("pacman", "-Qeq")) is not None:
      STATE.pkg_drift = sorted(set(explicit) - (STATE.pkgs | STATE.aur_pkgs))

    ql, qii = cmd_lines("pacman", "-Qlq"), cmd_lines("pacman", "-Qii")
    if ql is None or qii is None:
      return

    managed = set(decman.files) | set(decman.symlinks)
    prefixes = tuple(f"{d.rstrip('/')}/" for d in SYS_DIRS)
    owned = {p for p in ql if p.startswith(prefixes) and not p.endswith("/")}
    modified = {stripped.removesuffix("[modified]").strip() for raw in qii if (stripped := raw.strip()).startswith(prefixes) and stripped.endswith("[modified]")}

    for scan_root in SYS_DIRS:
      for root, dirs, files in os.walk(scan_root):
        dirs[:] = sorted(d for d in dirs if not IGNORE_RE.match(f"{root}/{d}"))
        for f in sorted(files):
          path = f"{root}/{f}"
          if not IGNORE_RE.match(path) and path not in managed and (path not in owned or path in modified):
            STATE.sys_drift.append(path)

  # --- Output, Hooks & Prompts ------------------------------------------------

  def run_hooks(hooks: list[str], label: str) -> None:
    for cmd in hooks:
      print(f"{c('[CONFIG]', 'cyan')} Running {label} hook: {c(cmd, 'bold')}")
      if (rc := subprocess.run(cmd, shell=True, check=False).returncode) != 0:
        print(f"  {c('[WARN]', 'bold', 'red')} {label} hook exited {rc}: {cmd}")

  def print_summary() -> None:
    def row(label: str, *parts: str) -> list[str]:
      items = [p for p in parts if p]
      return [f"  {label:<8} : {', '.join(items)}"] if items else []

    upgrade = c("on", "bold", "green") if STATE.upgrade else c("off", "dim")
    lines = [
      f"{c('[CONFIG]', 'bold', 'cyan')} {c('Summary:', 'bold')}",
      *row("modules", ", ".join(STATE.modules)),
      *row(
        "packages",
        f"{len(STATE.pkgs)} native" if STATE.pkgs else "",
        f"{len(STATE.aur_pkgs)} aur" if STATE.aur_pkgs else "",
        f"upgrade: {upgrade}",
      ),
      *row("units", f"{len(STATE.units)} system" if STATE.units else "", f"{len(STATE.user_units)} user" if STATE.user_units else ""),
      *row("files", f"{len(decman.files)} managed" if decman.files else "", f"{len(decman.symlinks)} symlinks" if decman.symlinks else ""),
      *row("hooks", f"{len(STATE.change_hooks)} on-change" if STATE.change_hooks else "", f"{len(STATE.post_hooks)} post-update" if STATE.post_hooks else ""),
      *row("pkgdrift", c(f"{len(STATE.pkg_drift)} unmanaged ({', '.join(STATE.pkg_drift)})", "yellow") if STATE.pkg_drift else ""),
      *row("sysdrift", c(f"{len(STATE.sys_drift)} unmanaged", "yellow") if STATE.sys_drift else ""),
    ]
    if not STATE.aur_ok and STATE.aur_pkgs:
      lines.append(f"  {c('[WARN]', 'bold', 'yellow')} AUR plugin unavailable (install devtools and re-run).")

    print("\n".join(lines))

  def _style_diff_line(line: str) -> str:
    text = line.rstrip("\n")
    styles = next((s for prefix, s in DIFF_COLORS.items() if line.startswith(prefix)), ("dim",))
    return f"  {c(text, *styles)}"

  def sync_live_edits() -> None:
    if not STATE.interactive:
      return

    for dst, entry in sorted(decman.files.items()):
      live = pathlib.Path(dst)
      if entry.bin_file or not entry.source_file or not live.is_file() or live.is_symlink():
        continue

      repo = pathlib.Path(entry.source_file)
      live_lines = live.read_text(errors="replace").splitlines(True)
      repo_lines = repo.read_text(errors="replace").splitlines(True)
      live_mode = live.stat().st_mode & 0o7777
      mode_changed = live_mode != entry.permissions

      if live_lines == repo_lines and not mode_changed:
        continue

      print(f"\n{c('[CONFIG]', 'bold', 'yellow')} Live edit detected in {c(live, 'bold')}:")
      if mode_changed:
        hint = f"mode: repo={oct(entry.permissions)} -> live={oct(live_mode)} (rename with {live_mode:04o}. prefix to persist)"
        print(f"  {c(hint, 'yellow')}")

      if live_lines != repo_lines:
        diff = difflib.unified_diff(repo_lines, live_lines, fromfile=f"repo:{repo}", tofile=f"live:{live}", n=2)
        print("\n".join(_style_diff_line(line) for line in diff))
        if prompt_yes(f"  {c('Pull live changes into repo? [y/N]', 'bold')}"):
          repo.write_text("".join(live_lines))

  def ensure_user_dir(path: pathlib.Path) -> None:
    missing = [p for p in (path, *path.parents) if not p.exists()]
    path.mkdir(parents=True, exist_ok=True)
    for created in reversed(missing):
      shutil.chown(created, user=USER, group=USER)

  def move_to_trash(src: pathlib.Path, stamp: str) -> None:
    files_dir, info_dir = TRASH_DIR / "files", TRASH_DIR / "info"
    ensure_user_dir(files_dir)
    ensure_user_dir(info_dir)

    name = src.name
    idx = 1
    while (files_dir / name).exists() or (files_dir / name).is_symlink() or (info_dir / f"{name}.trashinfo").exists():
      name = f"{src.stem}.{idx}{src.suffix}"
      idx += 1

    dest = files_dir / name
    info_file = info_dir / f"{name}.trashinfo"
    shutil.move(str(src), str(dest))
    info_file.write_text(f"[Trash Info]\nPath={urllib.parse.quote(str(src))}\nDeletionDate={stamp}\n")
    shutil.chown(info_file, user=USER, group=USER)
    shutil.chown(dest, user=USER, group=USER)

  def prompt_trash_drift() -> None:
    if not STATE.sys_drift:
      return

    print("\n".join(f"    {c('-', 'yellow')} {path}" for path in STATE.sys_drift))
    if not STATE.interactive or not prompt_yes("  Move unmanaged system paths to Trash? [y/N]"):
      return

    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    moved = 0
    for path in STATE.sys_drift:
      try:
        move_to_trash(pathlib.Path(path), stamp)
        moved += 1
      except (OSError, shutil.Error) as err:
        print(f"  {c('[WARN]', 'bold', 'red')} Failed to trash {path}: {err}")

    if moved:
      print(f"  {c('[CONFIG]', 'cyan')} Moved {moved} path(s) to {TRASH_DIR}")

  # --- Execution --------------------------------------------------------------

  load_modules()

  if not STATE.upgrade:
    pacman.commands.upgrade = lambda: ["true"]
  pacman.packages |= STATE.pkgs

  if (aur := decman.aur) is not None:
    aur.makepkg_user = USER
    aur.packages |= STATE.aur_pkgs
  else:
    decman.execution_order = [s for s in decman.execution_order if s != "aur"]

  if (systemd := decman.systemd) is not None:
    systemd.enabled_units |= STATE.units
    if STATE.user_units:
      systemd.enabled_user_units.setdefault(USER, set()).update(STATE.user_units)

  if STATE.change_hooks:
    decman.on_change(lambda *_: run_hooks(STATE.change_hooks, "on-change"))  # type: ignore[attr-defined]

  if STATE.post_hooks:
    decman.after_update(lambda *_: run_hooks(STATE.post_hooks, "post-update"))  # type: ignore[attr-defined]

  map_tree(FILES_DIR, "", "root")
  map_tree(HOME_DIR, str(HOME), USER, symlink=True)

  clean_broken_symlinks()
  run_audits()

  print_summary()
  sync_live_edits()
  prompt_trash_drift()


main()
