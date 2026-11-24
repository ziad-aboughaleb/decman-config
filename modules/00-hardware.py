"""Hardware: CPU microcode, GPU drivers, and laptop power tooling.

Unlike the other modules, this one detects hardware at import time and exposes
the same PACKAGES / SYSTEMD_UNITS interface so source.py treats it identically.
Stays importable without decman or root using stdlib only.
"""

import pathlib

CPUINFO = pathlib.Path("/proc/cpuinfo")
PCI_DEVICES = pathlib.Path("/sys/bus/pci/devices")
POWER_SUPPLY = pathlib.Path("/sys/class/power_supply")

# PCI vendor ID -> driver packages (32-bit Vulkan/ICD included for Steam/Proton)
GPU_PACKAGES: dict[str, set[str]] = {
  "0x1002": {"lib32-vulkan-icd-loader", "lib32-vulkan-radeon", "vulkan-radeon"},
  "0x10de": {"lib32-nvidia-utils", "nvidia-open-dkms", "nvidia-prime", "nvidia-utils"},
  "0x8086": {"lib32-vulkan-icd-loader", "lib32-vulkan-intel", "vulkan-intel"},
}


def _cpu_vendor() -> str | None:
  try:
    text = CPUINFO.read_text(encoding="utf-8", errors="replace").lower()
  except OSError:
    return None
  return next((v for v in ("intel", "amd") if v in text), None)


def _gpu_vendor_ids() -> set[str]:
  ids: set[str] = set()
  for device in PCI_DEVICES.glob("*"):
    try:
      if device.joinpath("class").read_text().strip().startswith("0x03"):
        ids.add(device.joinpath("vendor").read_text().strip().lower())
    except OSError:
      continue
  return ids


def _has_battery() -> bool:
  for supply in POWER_SUPPLY.glob("BAT*"):
    try:
      if supply.joinpath("type").read_text().strip().lower() == "battery":
        return True
    except OSError:
      continue
  return False


CPU_VENDOR = _cpu_vendor()
GPU_VENDORS = _gpu_vendor_ids()
IS_LAPTOP = _has_battery()

_packages: set[str] = set()
if CPU_VENDOR == "intel":
  _packages.add("intel-ucode")
elif CPU_VENDOR == "amd":
  _packages.add("amd-ucode")

for _vendor in GPU_VENDORS:
  _packages |= GPU_PACKAGES.get(_vendor, set())

SYSTEMD_UNITS: list[str] = []
if IS_LAPTOP:
  _packages |= {"brightnessctl", "powertop", "tlp"}
  SYSTEMD_UNITS.append("tlp.service")

PACKAGES: list[str] = sorted(_packages)

if __name__ == "__main__":
  print("cpu:", CPU_VENDOR)
  print("gpu vendor ids:", sorted(GPU_VENDORS))
  print("laptop:", IS_LAPTOP)
  print("packages:", PACKAGES)
  print("units:", SYSTEMD_UNITS)
