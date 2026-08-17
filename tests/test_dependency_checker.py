"""Unit tests for the toolchain dependency checker (GUI-004)."""

from picodesk.buildsys import dependency_checker as dc


def test_parse_version_arm_gcc_banner() -> None:
    line = "arm-none-eabi-gcc (Arm GNU Toolchain 12.2.Rel1 (Build arm-12.24)) 12.2.1 20221205"
    assert dc._parse_version(line)[:2] == (12, 2)


def test_parse_version_cmake_banner() -> None:
    assert dc._parse_version("cmake version 3.28.3") == (3, 28, 3)


def test_parse_version_rejects_garbage() -> None:
    assert dc._parse_version("no digits here") is None


def test_pico_sdk_version_from_submodule() -> None:
    status = dc.check_pico_sdk()
    if status.found:
        assert status.ok, f"pinned SDK below 1.5.1: {status.version}"


def test_builds_allowed_requires_all_required() -> None:
    good = dc.DependencyStatus("cmake", True, True, "3.28", True)
    bad = dc.DependencyStatus("arm-none-eabi-gcc", True, False, "", False)
    optional_bad = dc.DependencyStatus("matlab", False, False, "", False)
    assert dc.builds_allowed([good, optional_bad])
    assert not dc.builds_allowed([good, bad])


def test_python_gate_does_not_block_firmware_builds() -> None:
    py_bad = dc.DependencyStatus("python", True, True, "3.12.3", False)
    gcc = dc.DependencyStatus("arm-none-eabi-gcc", True, True, "12.2.1", True)
    assert dc.builds_allowed([py_bad, gcc])
