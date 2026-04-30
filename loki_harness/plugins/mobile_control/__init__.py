"""Plugin boundary for future migration of the mobile_control project."""

PLUGIN_STATUS = "planned"


def describe() -> dict[str, str]:
    return {
        "name": "mobile_control",
        "status": PLUGIN_STATUS,
        "role": "target_adapter",
        "notes": "Will host Xiaoyi/Claw device runtime and evidence collectors.",
    }
