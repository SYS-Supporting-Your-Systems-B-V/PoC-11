from app.config import ConfigMcsd


def test_config_mcsd_verify_ca_parses_boolean_strings_and_keeps_paths() -> None:
    false_cfg = ConfigMcsd(update_client_url="http://example.com/fhir", verify_ca="False")
    path_cfg = ConfigMcsd(update_client_url="http://example.com/fhir", verify_ca="ca.crt")

    assert false_cfg.verify_ca is False
    assert path_cfg.verify_ca == "ca.crt"
