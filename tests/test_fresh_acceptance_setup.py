from tools.native_fresh_setup import isolate_framework_config


def test_isolated_acceptance_never_starts_copied_downstream_adapters():
    original = {
        "platform": [{"id": "qq", "enable": True}, {"id": "wx", "enable": True}],
        "provider": [{"id": "chosen", "enable": True}],
        "persona": "native-persona",
        "provider_ltm_settings": {"enable": True},
    }
    isolated = isolate_framework_config(original)
    assert all(not p["enable"] for p in isolated["platform"])
    assert all(p["enable"] for p in original["platform"])
    assert isolated["provider"] == original["provider"]
    assert isolated["persona"] == original["persona"]
    assert isolated["provider_ltm_settings"] == original["provider_ltm_settings"]
