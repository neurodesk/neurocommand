import json
from pathlib import Path

from neurodesk.build_menu import visibility_flag


ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "neurodesk" / "icons"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

DIRECTORY_NAMES = [
    "Neurodesk",
    "All Applications",
    "Functional Imaging",
    "Workflows",
    "Cryo EM",
    "Data Organisation",
    "Diffusion Imaging",
    "Structural Imaging",
    "Quantitative Imaging",
    "Image Segmentation",
    "Image Registration",
    "Spectroscopy",
    "Rodent Imaging",
    "Image Reconstruction",
    "Visualization",
    "Programming",
    "Quality Control",
    "Shape Analysis",
    "Spine",
    "Electrophysiology",
    "BIDS Apps",
    "Machine Learning",
    "Body",
    "Hippocampus",
    "Phase Processing",
    "Molecular Biology",
    "Statistics",
]


def assert_png(path: Path) -> None:
    assert path.exists(), f"{path} is missing"
    assert path.read_bytes().startswith(PNG_MAGIC), f"{path} is not a PNG"


def visible_app_icon_paths() -> set[Path]:
    with (ROOT / "neurodesk" / "apps.json").open() as apps_json_file:
        menu_entries = json.load(apps_json_file)

    icon_paths = set()
    for menu_data in menu_entries.values():
        default_show_in_menu = visibility_flag(menu_data, "show_in_menu")
        for app_name, app_data in (menu_data.get("apps") or {}).items():
            if visibility_flag(app_data, "show_in_menu", default_show_in_menu):
                icon_paths.add(ICONS / f"{app_name.split()[0]}.png")
    return icon_paths


def directory_icon_paths() -> set[Path]:
    icon_paths = set()
    for name in DIRECTORY_NAMES:
        icon_name = "aedapt" if name == "Neurodesk" else name.lower().split()[0]
        icon_paths.add(ICONS / f"{icon_name}.png")
    return icon_paths


def test_visible_menu_icons_exist_and_are_pngs():
    icon_paths = visible_app_icon_paths()
    icon_paths.update(directory_icon_paths())
    icon_paths.update({ICONS / "Help.png", ICONS / "Update.png"})

    missing_or_invalid = []
    for icon_path in sorted(icon_paths):
        try:
            assert_png(icon_path)
        except AssertionError as error:
            missing_or_invalid.append(str(error))

    assert missing_or_invalid == []
