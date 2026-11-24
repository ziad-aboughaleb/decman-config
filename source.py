"""Decman declarative system engine driven by ./modules/*.py."""

import difflib
import glob
import json
import os
import pathlib
import re
import runpy
import subprocess
import sys

import decman

# --- Config & User Resolution -------------------------------------------------

store = pathlib.Path("/var/lib/decman/store.json")
saved = json.loads(store.read_text()).get("source_file") if store.exists() else None
CONFIG_DIR = pathlib.Path(saved).resolve().parent if saved and pathlib.Path(saved).exists() else pathlib.Path.cwd().resolve()
USER = os.environ.get("SUDO_USER") or "ziad"
HOME = f"/home/{USER}"


# --- System Drift Exclusions (/etc & /usr/local) ------------------------------

# fmt: off
IGNORED_PATHS = {
  match.rstrip("/")
  for pattern in (
    # Identity, auth & machine-unique state
    "/etc/passwd*", "/etc/shadow*", "/etc/group*", "/etc/gshadow*",
    "/etc/subuid*", "/etc/subgid*", "/etc/sudoers", "/etc/sudoers.d/**", "/etc/.pwd.lock",
    "/etc/fstab", "/etc/crypttab", "/etc/machine-id", "/etc/.updated",
    "/etc/hostname", "/etc/localtime", "/etc/locale.gen", "/etc/resolv.conf", "/etc/os-release",
    # Boot & snapshots
    "/etc/mkinitcpio.conf", "/etc/mkinitcpio.d/**", "/etc/conf.d/snapper",
    # Keys, certs & binary hardware/linker caches
    "/etc/ssh/ssh_host_*", "/etc/pacman.d/gnupg/**", "/etc/ssl/**", "/etc/ca-certificates/**",
    "/etc/ld.so.cache", "/etc/udev/hwdb.bin",
    # Stateful network, bluetooth & generated symlinks
    "/etc/NetworkManager/system-connections/**", "/etc/iwd/**", "/etc/bluetooth/**",
    "/etc/fonts/conf.d/**", "/etc/X11/xorg.conf.d/00-keyboard.conf",
    "/etc/pacman.d/*mirrorlist*", "/etc/pacman.d/*-backup",
    "/etc/systemd/*/*.wants/**", "/etc/systemd/*/*.service",
    # /usr/local desktop/mime caches
    "/usr/local/share/applications/mimeinfo.cache",
    "/usr/local/share/mime/**",
    # Generic backup, log & cache artifacts
    "/etc/**/*.pacsave", "/etc/**/*.pacnew", "/etc/**/*.old", "/etc/**/*.bak",
    "/etc/**/*.log", "/etc/**/*history*", "/etc/**/*cache*",
  )
  for match in glob.glob(pattern, recursive=True, include_hidden=True)
}
# fmt: on


# --- File Mapping, Live Diff & Drift Detection --------------------------------

BINARY_EXTS = {
  ".png",
  ".jpg",
  ".jpeg",
  ".webp",
  ".ico",
  ".gif",
  ".bin",
  ".ttf",
  ".otf",
  ".woff2",
}
MODE_PREFIX = re.compile(r"^([0-7]{3,4})\.(.+)$")


def is_interactive() -> bool:
  return sys.stdin.isatty() and "--dry-run" not in sys.argv and "--print" not in sys.argv


def map_tree(
  root: pathlib.Path,
  prefix: str,
  owner: str,
  *,
  symlink: bool = False,
) -> int:
  overrides = 0
  for path in sorted(root.rglob("*")):
    if not path.is_file():
      continue

    rel = path.relative_to(root)
    if symlink:
      decman.symlinks[f"{prefix}/{rel.as_posix()}"] = str(path)
      continue

    # Parse optional octal permission prefix (e.g. 440.wheel -> wheel, 0o440)
    if m := MODE_PREFIX.match(rel.name):
      dst_rel, mode = rel.with_name(m.group(2)).as_posix(), int(m.group(1), 8)
      overrides += 1
    else:
      dst_rel, mode = rel.as_posix(), 0o755 if os.access(path, os.X_OK) else 0o644

    dst = f"{prefix}/{dst_rel}"
    decman.files[dst] = decman.File(
      source_file=str(path),
      owner=owner,
      group=owner,
      permissions=mode,
      bin_file=path.suffix.lower() in BINARY_EXTS,
    )
  return overrides


def pull_live_edits() -> None:
  if not is_interactive():
    return

  for dst, entry in sorted(decman.files.items()):
    live_path = pathlib.Path(dst)
    if entry.bin_file or entry.source_file is None or not live_path.is_file() or live_path.is_symlink():
      continue

    repo_path = pathlib.Path(entry.source_file)
    live = live_path.read_text(errors="replace").splitlines(True)
    repo = repo_path.read_text(errors="replace").splitlines(True)
    if live != repo:
      diff = "".join(difflib.unified_diff(repo, live, fromfile=f"repo:{repo_path}", tofile=f"live:{dst}"))
      print(f"\n[CONFIG] Live edit detected in {dst}:\n{diff}", end="")
      if input("  Pull live changes into repo? [y/N] ").strip().lower() == "y":
        repo_path.write_text("".join(live))


def check_system_drift(managed: set[str]) -> None:
  if os.environ.get("DECMAN_SKIP_DRIFT") == "1":
    return

  # Query pacman ownership and modified backup files across scanned roots
  query = lambda args: subprocess.run(args, capture_output=True, text=True, check=False).stdout
  owned = {l for l in query(["pacman", "-Qlq"]).splitlines() if l.startswith(("/etc/", "/usr/local/")) and not l.endswith("/")}
  modified = {l.strip().removesuffix("[modified]").strip() for l in query(["pacman", "-Qii"]).splitlines() if l.strip().startswith(("/etc/", "/usr/local/")) and l.strip().endswith("[modified]")}

  drift = []
  for scan_root in ("/etc", "/usr/local"):
    if not os.path.isdir(scan_root):
      continue
    for root, dirs, files in os.walk(scan_root):
      dirs[:] = sorted(d for d in dirs if os.path.join(root, d) not in IGNORED_PATHS)
      for f in sorted(files):
        path = os.path.join(root, f)
        if path not in IGNORED_PATHS and path not in managed and (path not in owned or path in modified):
          drift.append(path)

  if not drift:
    print("  drift   : clean")
    return

  items = "\n".join(f"    - {p}" for p in drift)
  print(f"""\
  drift   : {len(drift)} unmanaged path(s)
{items}""")

  if is_interactive() and input("  Delete these paths? [y/N] ").strip().lower() == "y":
    for path in drift:
      try:
        os.unlink(path)
      except OSError as err:
        print(f"  [WARN] Failed to delete {path}: {err}")


# --- Entrypoint ---------------------------------------------------------------


def main() -> None:
  if decman.pacman is None:
    raise decman.SourceError("pacman plugin unavailable.")

  # Aggregate packages and units across ./modules/*.py
  mod_paths = [p for p in sorted((CONFIG_DIR / "modules").glob("*.py")) if not p.name.startswith("_")]
  packages, aur_packages, units, user_units = set(), set(), set(), set()

  for path in mod_paths:
    mod = runpy.run_path(str(path), init_globals={"USER": USER, "HOME": HOME})
    for pkg in mod.get("PACKAGES", ()):
      (aur_packages if pkg.startswith("aur:") else packages).add(pkg.removeprefix("aur:"))
    units.update(mod.get("SYSTEMD_UNITS", ()))
    user_units.update(mod.get("SYSTEMD_USER_UNITS", ()))

  upgrade = "upgrade" in sys.argv
  if not upgrade:
    decman.pacman.commands.upgrade = lambda: ["true"]

  decman.pacman.packages |= packages

  if decman.aur is None:
    decman.execution_order = [s for s in decman.execution_order if s != "aur"]
    print("[CONFIG] WARN: AUR plugin unavailable (install devtools and re-run).")
  else:
    decman.aur.makepkg_user = USER
    decman.aur.packages |= aur_packages

  if decman.systemd is not None:
    decman.systemd.enabled_units |= units
    if user_units:
      decman.systemd.enabled_user_units.setdefault(USER, set()).update(user_units)

  # Map system copies and home symlinks
  overrides = map_tree(CONFIG_DIR / "files", "", "root")
  map_tree(CONFIG_DIR / "home", HOME, USER, symlink=True)

  print(f"""\
[CONFIG] Summary:
  modules : {", ".join(p.stem for p in mod_paths)}
  packages: {len(packages)} native, {len(aur_packages)} aur (upgrade: {"on" if upgrade else "off"})
  units   : {len(units)} system, {len(user_units)} user
  files   : {len(decman.files)} managed ({overrides} custom modes), {len(decman.symlinks)} symlinks""")

  pull_live_edits()
  check_system_drift(set(decman.files) | set(decman.symlinks))


globals().update(locals())
main()
