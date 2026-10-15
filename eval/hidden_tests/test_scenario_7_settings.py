from pathlib import Path

from paystream import settings

DEPLOY_ENV = Path(settings.__file__).parents[2] / "deploy" / "production.env"


def test_production_defines_every_required_setting():
    defined = {
        line.split("=", 1)[0].strip()
        for line in DEPLOY_ENV.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    assert set(settings.REQUIRED_ENV) <= defined
