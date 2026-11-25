"""Decman declarative system engine driven by ./modules/*.py."""

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
import types

import decman

globals().update(locals())

# --- Config, User & Colors ----------------------------------------------------

store = pathlib.Path("/var/lib/decman/store.json")
saved = json.loads(store.read_text()).get("source_file") if store.exists() else None
here = pathlib.Path(__file__).resolve().parent if "__file__" in globals() else None

if here and (here / "modules").is_dir():
  CONFIG_DIR = here
elif saved and pathlib.Path(saved).exists():
  CONFIG_DIR = pathlib.Path(saved).resolve().parent
else:
  CONFIG_DIR = pathlib.Path.cwd().resolve()

MODULES_DIR = CONFIG_DIR / "modules"
FILES_DIR = CONFIG_DIR / "files"
HOME_DIR = CONFIG_DIR / "home"

SYS_DIRS = ("/etc", "/usr/local")

USER = os.environ.get("SUDO_USER") or "ziad"
HOME = f"/home/{USER}"

COLORS = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "cyan": "36"}


def c(text: object, *styles: str) -> str:
  codes = [COLORS[s] for s in styles if s in COLORS] if sys.stdout.isatty() else []
  return f"\033[{';'.join(codes)}m{text}\033[0m" if codes else str(text)


# --- System Drift Exclusions --------------------------------------------------

IGNORE_RE = re.compile(
  "|".join(
    fnmatch.translate(p.rstrip("/"))
    for p in (
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
  )
)


# --- Helpers ------------------------------------------------------------------

BINARY_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".ico", ".gif", ".bin", ".ttf", ".otf", ".woff2"}
MODE_PREFIX = re.compile(r"^([0-7]{3,4})\.(.+)$")
SKIP_WALK_DIRS = {".cache", ".git", ".cargo", ".rustup", ".npm", "Trash", "Steam", "umu", "node_modules"}


def check_arg(*args: str) -> bool:
  tokens = {part for arg in sys.argv[1:] for part in arg.split(",")}
  return bool(tokens.intersection(args))


def is_interactive() -> bool:
  return sys.stdin.isatty() and not check_arg("--dry-run", "--print")


def cmd_lines(*args: str) -> list[str] | None:
  res = subprocess.run(args, capture_output=True, text=True, check=False)
  return res.stdout.splitlines() if res.returncode == 0 else None


# --- Module Loading & File Mapping --------------------------------------------


def load_modules() -> types.SimpleNamespace:
  paths = [p for p in sorted(MODULES_DIR.glob("*.py")) if not p.name.startswith("_")]
  if not paths:
    raise decman.SourceError(f"No modules found in {MODULES_DIR}")

  pkgs, aur_pkgs, units, user_units, change_hooks, post_hooks = set(), set(), set(), set(), [], []
  for path in paths:
    mod = runpy.run_path(str(path), init_globals={"USER": USER, "HOME": HOME})
    for pkg in mod.get("PACKAGES", ()):
      (aur_pkgs if pkg.startswith("aur:") else pkgs).add(pkg.removeprefix("aur:"))
    units.update(mod.get("SYSTEMD_UNITS", ()))
    user_units.update(mod.get("SYSTEMD_USER_UNITS", ()))
    change_hooks.extend(mod.get("CHANGE_HOOKS", ()))
    post_hooks.extend(mod.get("POST_HOOKS", ()))

  return types.SimpleNamespace(
    names=[p.stem for p in paths],
    pkgs=pkgs,
    aur_pkgs=aur_pkgs,
    units=units,
    user_units=user_units,
    change_hooks=list(dict.fromkeys(change_hooks)),
    post_hooks=list(dict.fromkeys(post_hooks)),
  )


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

    if m := MODE_PREFIX.match(rel.name):
      dst_rel, mode = rel.with_name(m.group(2)).as_posix(), int(m.group(1), 8)
    else:
      dst_rel, mode = rel.as_posix(), 0o755 if os.access(path, os.X_OK) else 0o644

    decman.files[f"{prefix}/{dst_rel}"] = decman.File(
      source_file=str(path),
      owner=owner,
      group=owner,
      permissions=mode,
      bin_file=path.suffix.lower() in BINARY_EXTS,
    )


# --- Audits -------------------------------------------------------------------


def find_pkg_drift(declared: set[str]) -> list[str]:
  explicit = cmd_lines("pacman", "-Qeq")
  return sorted(set(explicit) - declared) if explicit is not None else []


def find_broken_symlinks() -> list[str]:
  broken: list[str] = []
  stack = [HOME]
  while stack:
    root = stack.pop()
    try:
      with os.scandir(root) as it:
        for entry in it:
          if entry.is_symlink():
            if not os.path.exists(entry.path):
              target = pathlib.Path(os.path.abspath(os.path.join(root, os.readlink(entry.path))))
              if target.is_relative_to(CONFIG_DIR):
                broken.append(entry.path)
          elif entry.is_dir(follow_symlinks=False) and entry.name not in SKIP_WALK_DIRS:
            stack.append(entry.path)
    except OSError:
      continue
  return sorted(broken)


def find_sys_drift(managed: set[str]) -> list[str]:
  ql, qii = cmd_lines("pacman", "-Qlq"), cmd_lines("pacman", "-Qii")
  if ql is None or qii is None:
    return []

  prefixes = tuple(f"{d.rstrip('/')}/" for d in SYS_DIRS)
  owned = {p for p in ql if p.startswith(prefixes) and not p.endswith("/")}
  modified = {line.removesuffix("[modified]").strip() for raw in qii if (line := raw.strip()).startswith(prefixes) and line.endswith("[modified]")}

  drift = []
  for scan_root in SYS_DIRS:
    for root, dirs, files in os.walk(scan_root):
      dirs[:] = sorted(d for d in dirs if not IGNORE_RE.match(f"{root}/{d}"))
      for f in sorted(files):
        path = f"{root}/{f}"
        if not IGNORE_RE.match(path) and path not in managed and (path not in owned or path in modified):
          drift.append(path)
  return drift


# --- Output, Hooks & Prompts --------------------------------------------------


def run_hooks(hooks: list[str], label: str) -> None:
  for cmd in hooks:
    print(f"{c('[CONFIG]', 'cyan')} Running {label} hook: {c(cmd, 'bold')}")
    if (rc := subprocess.run(cmd, shell=True, check=False).returncode) != 0:
      print(f"  {c('[WARN]', 'bold', 'red')} {label} hook exited {rc}: {cmd}")


def print_summary(
  state: types.SimpleNamespace,
  upgrade: bool,
  aur_ok: bool,
  pkg_drift: list[str],
  broken_links: list[str],
  sys_drift: list[str],
) -> None:
  ok = c("clean", "green")
  print(f"""\
{c("[CONFIG]", "bold", "cyan")} {c("Summary:", "bold")}
  modules  : {", ".join(state.names)}
  packages : {len(state.pkgs)} native, {len(state.aur_pkgs)} aur (upgrade: {c("on", "bold", "green") if upgrade else c("off", "dim")})
  units    : {len(state.units)} system, {len(state.user_units)} user
  files    : {len(decman.files)} managed, {len(decman.symlinks)} symlinks
  hooks    : {len(state.change_hooks)} on-change, {len(state.post_hooks)} post-update
  pkgdrift : {c(f"{len(pkg_drift)} unmanaged ({', '.join(pkg_drift)})", "yellow") if pkg_drift else ok}
  symlinks : {c(f"{len(broken_links)} broken", "yellow") if broken_links else ok}
  drift    : {c(f"{len(sys_drift)} unmanaged", "yellow") if sys_drift else ok}""")
  if not aur_ok:
    print(f"  {c('[WARN]', 'bold', 'yellow')} AUR plugin unavailable (install devtools and re-run).")


def sync_live_edits() -> None:
  if not is_interactive():
    return
  for dst, entry in sorted(decman.files.items()):
    live = pathlib.Path(dst)
    if entry.bin_file or not entry.source_file or not live.is_file() or live.is_symlink():
      continue
    repo = pathlib.Path(entry.source_file)
    live_lines = live.read_text(errors="replace").splitlines(True)
    repo_lines = repo.read_text(errors="replace").splitlines(True)
    if live_lines != repo_lines:
      diff = "".join(difflib.unified_diff(repo_lines, live_lines, fromfile=f"repo:{repo}", tofile=f"live:{live}"))
      print(f"\n{c('[CONFIG]', 'bold', 'yellow')} Live edit detected in {c(live, 'bold')}:\n{diff}", end="")
      if input("  Pull live changes into repo? [y/N] ").strip().lower() == "y":
        repo.write_text("".join(live_lines))


def prompt_unlink(paths: list[str], prompt: str, *, backup: bool = False) -> None:
  if not paths:
    return
  for path in paths:
    print(f"    {c('-', 'yellow')} {path}")
  if is_interactive() and input(f"  {prompt} [y/N] ").strip().lower() == "y":
    trash_dir = None
    if backup:
      stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
      trash_dir = pathlib.Path(f"/tmp/decman-trash/{stamp}")

    for path in paths:
      try:
        if trash_dir:
          dest = trash_dir / path.lstrip("/")
          dest.parent.mkdir(parents=True, exist_ok=True)
          shutil.move(path, dest)
        else:
          os.unlink(path)
      except OSError as err:
        print(f"  {c('[WARN]', 'bold', 'red')} Failed to remove {path}: {err}")

    if trash_dir and trash_dir.exists():
      print(f"  {c('[INFO]', 'cyan')} Moved removed files to {trash_dir}")


# --- Entrypoint ---------------------------------------------------------------


def main() -> None:
  if decman.pacman is None:
    raise decman.SourceError("pacman plugin unavailable.")

  state = load_modules()

  upgrade = check_arg("upgrade")
  if not upgrade:
    decman.pacman.commands.upgrade = lambda: ["true"]
  decman.pacman.packages |= state.pkgs

  aur_ok = (aur := decman.aur) is not None
  if aur is not None:
    aur.makepkg_user = USER
    aur.packages |= state.aur_pkgs
  else:
    decman.execution_order = [s for s in decman.execution_order if s != "aur"]

  if (systemd := decman.systemd) is not None:
    systemd.enabled_units |= state.units
    if state.user_units:
      systemd.enabled_user_units.setdefault(USER, set()).update(state.user_units)

  if state.change_hooks:
    decman.on_change(lambda *_: run_hooks(state.change_hooks, "on-change"))  # type: ignore[attr-defined]

  if state.post_hooks:
    decman.after_update(lambda *_: run_hooks(state.post_hooks, "post-update"))  # type: ignore[attr-defined]

  map_tree(FILES_DIR, "", "root")
  map_tree(HOME_DIR, HOME, USER, symlink=True)

  pkg_drift = find_pkg_drift(state.pkgs | state.aur_pkgs)
  broken_links = find_broken_symlinks()
  sys_drift = find_sys_drift(set(decman.files) | set(decman.symlinks))

  print_summary(state, upgrade, aur_ok, pkg_drift, broken_links, sys_drift)
  sync_live_edits()
  prompt_unlink(broken_links, "Remove broken symlinks?")
  prompt_unlink(sys_drift, "Delete unmanaged system paths?", backup=True)


globals().update(locals())
main()
