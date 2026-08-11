from neurodesk.write_log import is_primary_container_app


def test_hyphenated_named_variant_is_a_primary_container_app():
    assert is_primary_container_app(
        "neurodesktop-lite_arm64", "neurodesktop-lite_arm64 20260428"
    )


def test_gui_sub_app_is_not_a_primary_container_app():
    assert not is_primary_container_app("fsl", "fsleyesGUI-fsl 6.0.7.22")
